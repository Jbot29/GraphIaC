"""AWS Batch — a queue that turns into computers when there is work.

This is the executor every bioinformatics workflow engine targets
(Nextflow `awsbatch`, Snakemake `aws-batch`, Cromwell, Step Functions), and
the thing every lab rebuilds by hand: a compute environment, a job queue,
a job definition, three IAM roles, a launch template because the default
30 GB root disk fills with container images on the first real run, a
security group, and a Spot allocation strategy copied from a blog post.

Three nodes, three arrows:

    spot  : BatchComputeEnv(capacity: "spot", max_vcpus: 256,
                            vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids)
    queue : BatchJobQueue
    align : BatchJobDef(image: images.uri, vcpus: 4, memory_mb: 16384)

    spot     -> queue    BatchEnvQueueEdge — the queue dispatches to this
                         environment (⊘ the queue waits until it is VALID)
    job-role -> align    IAMRoleBatchEdge — pull the image, write logs
    align    -> data     BatchS3Edge — read/write exactly that bucket

What the compute environment owns, so you don't: its security group (no
ingress; arrows open ports), the EC2 instance role and profile, a launch
template with a root volume you choose (`root_volume_gb`, default 100), the
Batch service-linked role, and tags on the instances themselves — so the
bill can be split by `project` without anyone tagging a machine by hand.

Capacity is one word. `"spot"` is the default because batch work is the
textbook Spot workload: interruptible, retried by the queue, 60–90 % off.
`"on-demand"` when a job can't be interrupted; `"fargate"` / `"fargate-spot"`
when there is no instance to manage and the job fits in 16 vCPUs.

Jobs are submitted by the workflow engine, not by this file — a job
definition is the shape of a job, not a run. Nextflow, for example, needs
only the queue's name and a bucket for its work directory; see
`examples/batch-lab/`.
"""

import re
import time
from typing import ClassVar, Dict, List, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode, VerifyResult

from ..logs import setup_logger
from .ec2.network import (
    NETWORK_ACTIONS,
    delete_security_group,
    ensure_security_group,
    has_ingress,
    resolve_network,
    subnets_map_public_ip,
)
from .iam_policy import (
    IamPolicyDocument,
    IamPolicyStatement,
    IamTrustPolicyDocument,
    IamTrustPolicyStatement,
    get_inline_policy_for_role,
    put_inline_policy_for_role,
)
from .iam_role import IAMRoleInlinePolicyEdge
from .types import AwsName

logger = setup_logger()

CAPACITY = {
    # capacity word -> (Batch CR type, allocation strategy)
    "spot": ("SPOT", "SPOT_PRICE_CAPACITY_OPTIMIZED"),
    "on-demand": ("EC2", "BEST_FIT_PROGRESSIVE"),
    "fargate": ("FARGATE", None),
    "fargate-spot": ("FARGATE_SPOT", None),
}
CAPACITY_BY_TYPE = {v[0]: k for k, v in CAPACITY.items()}
EC2_CAPACITY = ("spot", "on-demand")

EC2_TRUST_POLICY = IamTrustPolicyDocument(Statement=[IamTrustPolicyStatement(
    Sid="GraphIaCTrustEc2", Effect="Allow",
    Principal={"Service": "ec2.amazonaws.com"}, Action="sts:AssumeRole")])

# Lets the ECS agent on a Batch instance register and pull images.
INSTANCE_POLICY = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
# Lets a job pull its image from ECR and write to CloudWatch Logs.
TASK_EXECUTION_POLICY = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"

# EC2 quota: "All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests", in vCPUs.
SPOT_VCPU_QUOTA = "L-34B43A08"
ONDEMAND_VCPU_QUOTA = "L-1216C47A"

# Environment variables whose *name* says they hold a secret. A job
# definition is readable by anyone with batch:DescribeJobDefinitions, which
# is everyone on the team — secrets belong in Secrets Manager / SSM.
SECRET_NAME = re.compile(r"(SECRET|PASSWORD|PASSWD|TOKEN|API_KEY|PRIVATE_KEY|ACCESS_KEY)", re.I)


def _user_tags(tags: dict) -> Dict[str, str]:
    return {k: v for k, v in (tags or {}).items() if k != "ManagedBy" and not k.startswith("graphiac:")}


def _managed_tags(extra: Dict[str, str]) -> Dict[str, str]:
    return {"ManagedBy": "GraphIaC", **(extra or {})}


def _batch_service_role_arn(session) -> str:
    """The Batch service-linked role, created on first use. AWS creates it
    for you when serviceRole is omitted; naming it explicitly is what every
    emulator and most least-privilege deploy policies expect."""
    iam = session.client("iam")
    try:
        return iam.create_service_linked_role(AWSServiceName="batch.amazonaws.com")["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("InvalidInput", "EntityAlreadyExists"):
            raise
    return iam.get_role(RoleName="AWSServiceRoleForBatch")["Role"]["Arn"]


def _wait(describe, done, attempts=60, delay=5, what="resource"):
    """Poll `describe()` until `done(item)` (item may be None when gone)."""
    for _ in range(attempts):
        if done(describe()):
            return True
        time.sleep(delay)
    logger.warning(f"Timed out waiting for {what}")
    return False


# ====================================================================== env


class BatchComputeEnv(BaseNode):
    deploy_actions: ClassVar[list] = [
        "batch:CreateComputeEnvironment", "batch:DescribeComputeEnvironments",
        "batch:UpdateComputeEnvironment", "batch:DeleteComputeEnvironment", "batch:TagResource",
        "iam:CreateServiceLinkedRole", "iam:GetRole", "iam:CreateRole", "iam:DeleteRole",
        "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
        "iam:CreateInstanceProfile", "iam:DeleteInstanceProfile", "iam:GetInstanceProfile",
        "iam:AddRoleToInstanceProfile", "iam:RemoveRoleFromInstanceProfile", "iam:PassRole",
        "ec2:CreateLaunchTemplate", "ec2:CreateLaunchTemplateVersion", "ec2:DeleteLaunchTemplate",
        "ec2:DescribeLaunchTemplates", "ec2:DescribeLaunchTemplateVersions",
        "servicequotas:GetServiceQuota",
    ] + NETWORK_ACTIONS

    name: AwsName
    region: str = "us-east-2"

    capacity: str = "spot"            # spot | on-demand | fargate | fargate-spot
    max_vcpus: int = 256
    # EC2 capacity only. Unset means: optimal instances, scale to zero,
    # a 100 GB root volume (the ECS-optimized AMI's 30 GB fills with images).
    instance_types: Optional[List[str]] = None
    min_vcpus: Optional[int] = None
    root_volume_gb: Optional[int] = None
    tags: Dict[str, str] = {}

    vpc_id: Optional[str] = None
    subnet_ids: Optional[List[str]] = None

    # populated by AWS
    arn: Optional[str] = None
    status: Optional[str] = None
    state: Optional[str] = None
    security_group_id: Optional[str] = None
    instance_profile_arn: Optional[str] = None
    launch_template_id: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    @property
    def is_ec2(self) -> bool:
        return self.capacity in EC2_CAPACITY

    @property
    def sg_name(self) -> str:
        return f"{self.name}-batch-sg"

    @property
    def instance_role_name(self) -> str:
        return f"{self.name}-instance-role"

    @property
    def launch_template_name(self) -> str:
        return f"{self.name}-lt"

    def ready(self) -> bool:
        """A queue cannot be created against an environment that is still
        CREATING; the planner holds the queue (via the gating arrow) until
        this is VALID."""
        return self.status == "VALID" and self.state == "ENABLED"

    # ------------------------------------------------------------------ read

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        batch = session.client("batch", region_name=region)
        envs = [e for e in batch.describe_compute_environments(
            computeEnvironments=[read_id])["computeEnvironments"] if e.get("status") != "DELETED"]
        if not envs:
            return None
        env = envs[0]
        cr = env.get("computeResources") or {}
        capacity = CAPACITY_BY_TYPE.get(cr.get("type"), "spot")
        is_ec2 = capacity in EC2_CAPACITY

        root_gb, lt_id = None, (cr.get("launchTemplate") or {}).get("launchTemplateId")
        ec2 = session.client("ec2", region_name=region)
        if lt_id:
            try:
                versions = ec2.describe_launch_template_versions(LaunchTemplateId=lt_id)["LaunchTemplateVersions"]
                data = max(versions, key=lambda v: v["VersionNumber"])["LaunchTemplateData"]
                for m in data.get("BlockDeviceMappings", []):
                    if m.get("Ebs", {}).get("VolumeSize"):
                        root_gb = m["Ebs"]["VolumeSize"]
            except ClientError as e:
                logger.debug(f"Could not read launch template {lt_id}: {e}")

        vpc_id = None
        if cr.get("subnets"):
            try:
                vpc_id = ec2.describe_subnets(SubnetIds=cr["subnets"][:1])["Subnets"][0]["VpcId"]
            except (ClientError, IndexError, KeyError):
                pass

        sgs = cr.get("securityGroupIds") or []
        return cls(
            g_id=g_id, name=read_id, region=region,
            capacity=capacity,
            max_vcpus=cr.get("maxvCpus", 0),
            instance_types=cr.get("instanceTypes") if is_ec2 else None,
            min_vcpus=cr.get("minvCpus") if is_ec2 else None,
            root_volume_gb=root_gb,
            tags=_user_tags(env.get("tags")),
            vpc_id=vpc_id,
            subnet_ids=cr.get("subnets"),
            arn=env.get("computeEnvironmentArn"),
            status=env.get("status"),
            state=env.get("state"),
            security_group_id=sgs[0] if sgs else None,
            instance_profile_arn=cr.get("instanceRole"),
            launch_template_id=lt_id,
        )

    # ---------------------------------------------------------------- create

    def _ensure_instance_profile(self, session) -> str:
        """The role the EC2 instances run as — the ECS agent needs it to
        register with the cluster Batch manages. Not the job's role."""
        iam = session.client("iam")
        name = self.instance_role_name
        try:
            iam.create_role(RoleName=name,
                            AssumeRolePolicyDocument=EC2_TRUST_POLICY.model_dump_json(exclude_none=True),
                            Description=f"GraphIaC: instance role for Batch environment {self.name}",
                            Tags=[{"Key": k, "Value": v} for k, v in _managed_tags(self.tags).items()])
            logger.info(f"Created instance role {name}")
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
        attached = {p["PolicyArn"] for p in iam.list_attached_role_policies(RoleName=name)["AttachedPolicies"]}
        if INSTANCE_POLICY not in attached:
            iam.attach_role_policy(RoleName=name, PolicyArn=INSTANCE_POLICY)
        try:
            profile = iam.create_instance_profile(InstanceProfileName=name)["InstanceProfile"]
            iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
            time.sleep(0)  # IAM propagation is handled by Batch's own retry on CREATING
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
            profile = iam.get_instance_profile(InstanceProfileName=name)["InstanceProfile"]
            if not any(r["RoleName"] == name for r in profile.get("Roles", [])):
                iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
        return profile["Arn"]

    def _delete_instance_profile(self, session):
        iam = session.client("iam")
        name = self.instance_role_name
        for fn in (
            lambda: iam.remove_role_from_instance_profile(InstanceProfileName=name, RoleName=name),
            lambda: iam.delete_instance_profile(InstanceProfileName=name),
            lambda: iam.detach_role_policy(RoleName=name, PolicyArn=INSTANCE_POLICY),
            lambda: iam.delete_role(RoleName=name),
        ):
            try:
                fn()
            except ClientError as e:
                if e.response["Error"]["Code"] != "NoSuchEntity":
                    logger.warning(f"Cleaning up {name}: {e.response['Error']['Code']}")

    def _launch_template_data(self):
        return {"BlockDeviceMappings": [{
            "DeviceName": "/dev/xvda",
            "Ebs": {"VolumeSize": self.root_volume_gb or 100, "VolumeType": "gp3",
                    "DeleteOnTermination": True, "Encrypted": True},
        }]}

    def _ensure_launch_template(self, session) -> str:
        ec2 = session.client("ec2", region_name=self.region)
        try:
            found = ec2.describe_launch_templates(LaunchTemplateNames=[self.launch_template_name])["LaunchTemplates"]
            return found[0]["LaunchTemplateId"]
        except ClientError as e:
            if "NotFound" not in e.response["Error"]["Code"]:
                raise
        resp = ec2.create_launch_template(
            LaunchTemplateName=self.launch_template_name,
            LaunchTemplateData=self._launch_template_data(),
            TagSpecifications=[{"ResourceType": "launch-template",
                                "Tags": [{"Key": k, "Value": v} for k, v in _managed_tags(self.tags).items()]}],
        )
        logger.info(f"Created launch template {self.launch_template_name} "
                    f"({self.root_volume_gb or 100} GB encrypted gp3 root volume)")
        return resp["LaunchTemplate"]["LaunchTemplateId"]

    def _compute_resources(self, subnet_ids):
        cr_type, strategy = CAPACITY[self.capacity]
        cr = {
            "type": cr_type,
            "maxvCpus": self.max_vcpus,
            "subnets": subnet_ids,
            "securityGroupIds": [self.security_group_id],
        }
        if self.is_ec2:
            cr.update(
                allocationStrategy=strategy,
                minvCpus=self.min_vcpus or 0,
                instanceTypes=self.instance_types or ["optimal"],
                instanceRole=self.instance_profile_arn,
                launchTemplate={"launchTemplateId": self.launch_template_id, "version": "$Latest"},
                # lands on every instance Batch launches — cost allocation for free
                tags=_managed_tags({**self.tags, "graphiac:batch-env": self.name}),
            )
        return cr

    def create(self, session, G):
        if self.capacity not in CAPACITY:
            logger.error(f"{self.name}: capacity must be one of {', '.join(CAPACITY)}")
            return False
        vpc_id, subnet_ids = resolve_network(session, self.region, self.vpc_id, self.subnet_ids)
        if not vpc_id or not subnet_ids:
            logger.error(f"{self.name}: no usable subnets in {self.region}")
            return False
        self.vpc_id, self.subnet_ids = vpc_id, subnet_ids

        # Empty on purpose: Batch instances need no ingress to do their job.
        self.security_group_id = ensure_security_group(
            session, self.region, self.sg_name, f"GraphIaC: Batch instances for {self.name}", vpc_id)
        if self.is_ec2:
            self.instance_profile_arn = self._ensure_instance_profile(session)
            self.launch_template_id = self._ensure_launch_template(session)

        batch = session.client("batch", region_name=self.region)
        resp = batch.create_compute_environment(
            computeEnvironmentName=self.name,
            type="MANAGED",
            state="ENABLED",
            serviceRole=_batch_service_role_arn(session),
            computeResources=self._compute_resources(subnet_ids),
            tags=_managed_tags(self.tags),
        )
        self.arn = resp["computeEnvironmentArn"]
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        self.status, self.state = (live.status, live.state) if live else ("CREATING", "ENABLED")
        logger.info(f"Created Batch compute environment {self.name} ({self.capacity}, up to {self.max_vcpus} vCPUs)"
                    + (" — the queue stays BLOCKED until it is VALID" if self.status != "VALID" else ""))
        return True

    def update(self, session, G, diff=None):
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        if not live:
            return
        if live.capacity != self.capacity:
            logger.error(f"{self.name}: capacity cannot change in place ({live.capacity} -> {self.capacity}); "
                         f"give the environment a new name to replace it")
            self.capacity = live.capacity
        batch = session.client("batch", region_name=self.region)
        cr = {"maxvCpus": self.max_vcpus}
        if self.is_ec2:
            cr["minvCpus"] = self.min_vcpus or 0
            cr["instanceTypes"] = self.instance_types or ["optimal"]
            if self.root_volume_gb and live.root_volume_gb and self.root_volume_gb != live.root_volume_gb:
                ec2 = session.client("ec2", region_name=self.region)
                ec2.create_launch_template_version(LaunchTemplateId=live.launch_template_id,
                                                   LaunchTemplateData=self._launch_template_data())
                cr["launchTemplate"] = {"launchTemplateId": live.launch_template_id, "version": "$Latest"}
                cr["updateToLatestImageVersion"] = True
                logger.info(f"{self.name}: new launch template version with a {self.root_volume_gb} GB root volume; "
                            f"Batch rolls the instances")
        try:
            batch.update_compute_environment(computeEnvironment=self.name, state="ENABLED", computeResources=cr)
        except ClientError as e:
            logger.error(f"Could not update {self.name}: {e}")
        logger.info(f"Updated Batch compute environment {self.name}")

    def delete(self, session, G):
        batch = session.client("batch", region_name=self.region)

        def live():
            envs = [e for e in batch.describe_compute_environments(
                computeEnvironments=[self.name])["computeEnvironments"] if e.get("status") != "DELETED"]
            return envs[0] if envs else None

        if live():
            try:
                batch.update_compute_environment(computeEnvironment=self.name, state="DISABLED")
                _wait(live, lambda e: e is None or e.get("status") in ("VALID", "INVALID"),
                      what=f"{self.name} to disable")
                batch.delete_compute_environment(computeEnvironment=self.name)
                _wait(live, lambda e: e is None, what=f"{self.name} to delete")
                logger.info(f"Deleted Batch compute environment {self.name}")
            except ClientError as e:
                logger.error(f"Could not delete {self.name}: {e} — is a job queue still using it?")
                return
        if self.is_ec2:
            ec2 = session.client("ec2", region_name=self.region)
            try:
                ec2.delete_launch_template(LaunchTemplateName=self.launch_template_name)
            except ClientError as e:
                logger.debug(f"launch template: {e}")
            self._delete_instance_profile(session)
        if self.security_group_id:
            delete_security_group(session, self.region, self.security_group_id)

    # ---------------------------------------------------------------- verify

    def _vcpu_quota(self, session) -> Optional[float]:
        code = SPOT_VCPU_QUOTA if self.capacity == "spot" else ONDEMAND_VCPU_QUOTA
        try:
            sq = session.client("service-quotas", region_name=self.region)
            return sq.get_service_quota(ServiceCode="ec2", QuotaCode=code)["Quota"]["Value"]
        except Exception as e:  # not readable here (emulators, missing permission) — say nothing
            logger.debug(f"Could not read vCPU quota: {e}")
            return None

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        tag = f"batch-env:{self.name}"
        if not live:
            return [VerifyResult(name=tag, passed=False, message="compute environment does not exist")]
        results = [VerifyResult(
            name=f"{tag}:valid", passed=live.status == "VALID" and live.state == "ENABLED",
            message=f"{live.status}/{live.state}")]
        if live.security_group_id:
            world = any(has_ingress(session, self.region, live.security_group_id, p, cidr="0.0.0.0/0")
                        for p in (22, 2375, 2376))
            results.append(VerifyResult(
                name=f"{tag}:no-world-ingress", passed=not world,
                message="SSH/Docker OPEN TO 0.0.0.0/0" if world else "no ingress from the internet"))
        if live.is_ec2 and live.subnet_ids:
            public = subnets_map_public_ip(session, self.region, live.subnet_ids)
            results.append(VerifyResult(
                name=f"{tag}:private-subnets", passed=not public,
                message="instances get public IPs — fine in the default VPC, not behind a boundary"
                        if public else "instances launch without public IPs"))
            quota = self._vcpu_quota(session)
            if quota is not None:
                results.append(VerifyResult(
                    name=f"{tag}:vcpu-quota", passed=live.max_vcpus <= quota,
                    message=f"max_vcpus {live.max_vcpus} within the {int(quota)} vCPU quota" if live.max_vcpus <= quota
                            else f"max_vcpus {live.max_vcpus} exceeds the account's {int(quota)} vCPU quota — "
                                 f"jobs will sit RUNNABLE; request an increase in Service Quotas"))
        return results


# ==================================================================== queue


class BatchJobQueue(BaseNode):
    deploy_actions: ClassVar[list] = [
        "batch:CreateJobQueue", "batch:DescribeJobQueues", "batch:UpdateJobQueue",
        "batch:DeleteJobQueue", "batch:TagResource",
    ]

    name: AwsName
    region: str = "us-east-2"
    priority: int = 10
    tags: Dict[str, str] = {}

    # populated by AWS
    arn: Optional[str] = None
    status: Optional[str] = None
    state: Optional[str] = None
    compute_env_arns: Optional[List[str]] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    def ready(self) -> bool:
        return self.status == "VALID" and self.state == "ENABLED"

    def _env_order(self, G):
        """[(order, env node)] from the incoming `env -> queue` arrows."""
        found = []
        for src, _, data in G.in_edges(self.g_id, data=True):
            edge = data["data"]
            if isinstance(edge, BatchEnvQueueEdge):
                found.append((edge.order, G.nodes[src]["data"]))
        return sorted(found, key=lambda t: t[0])

    def _order_payload(self, G):
        return [{"order": order, "computeEnvironment": env.arn or env.name} for order, env in self._env_order(G)]

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        batch = session.client("batch", region_name=region)
        queues = [q for q in batch.describe_job_queues(jobQueues=[read_id])["jobQueues"]
                  if q.get("status") != "DELETED"]
        if not queues:
            return None
        q = queues[0]
        return cls(
            g_id=g_id, name=read_id, region=region,
            priority=q.get("priority", 10),
            tags=_user_tags(q.get("tags")),
            arn=q.get("jobQueueArn"), status=q.get("status"), state=q.get("state"),
            compute_env_arns=[o["computeEnvironment"] for o in
                              sorted(q.get("computeEnvironmentOrder", []), key=lambda o: o["order"])],
        )

    def create(self, session, G):
        order = self._order_payload(G)
        if not order:
            logger.error(f"{self.name} has no compute environment — add a `<BatchComputeEnv> -> {self.g_id}` arrow")
            return False
        batch = session.client("batch", region_name=self.region)
        resp = batch.create_job_queue(
            jobQueueName=self.name, state="ENABLED", priority=self.priority,
            computeEnvironmentOrder=order, tags=_managed_tags(self.tags))
        self.arn = resp["jobQueueArn"]
        self.compute_env_arns = [o["computeEnvironment"] for o in order]
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        self.status, self.state = (live.status, live.state) if live else ("CREATING", "ENABLED")
        logger.info(f"Created Batch job queue {self.name} -> {', '.join(e.name for _, e in self._env_order(G))}")
        return True

    def update(self, session, G, diff=None):
        batch = session.client("batch", region_name=self.region)
        kwargs = {"jobQueue": self.name, "state": "ENABLED", "priority": self.priority}
        order = self._order_payload(G)
        if order:
            kwargs["computeEnvironmentOrder"] = order
        batch.update_job_queue(**kwargs)
        logger.info(f"Updated Batch job queue {self.name}")

    def delete(self, session, G):
        batch = session.client("batch", region_name=self.region)

        def live():
            qs = [q for q in batch.describe_job_queues(jobQueues=[self.name])["jobQueues"]
                  if q.get("status") != "DELETED"]
            return qs[0] if qs else None

        if not live():
            return
        try:
            batch.update_job_queue(jobQueue=self.name, state="DISABLED")
            _wait(live, lambda q: q is None or q.get("status") in ("VALID", "INVALID"), what=f"{self.name} to disable")
            batch.delete_job_queue(jobQueue=self.name)
            _wait(live, lambda q: q is None, what=f"{self.name} to delete")
            logger.info(f"Deleted Batch job queue {self.name}")
        except ClientError as e:
            logger.error(f"Could not delete {self.name}: {e}")

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        tag = f"batch-queue:{self.name}"
        if not live:
            return [VerifyResult(name=tag, passed=False, message="job queue does not exist")]
        return [
            VerifyResult(name=f"{tag}:valid", passed=live.ready(), message=f"{live.status}/{live.state}"),
            VerifyResult(name=f"{tag}:has-compute", passed=bool(live.compute_env_arns),
                         message=f"{len(live.compute_env_arns or [])} compute environment(s)"),
        ]


class BatchEnvQueueEdge(BaseEdge):
    """`env -> queue`: the queue dispatches to this environment, in this
    `order` when it has several. Gates the queue: Batch refuses to create a
    queue against an environment that is still CREATING, so the planner
    holds the queue BLOCKED until the environment reads VALID."""

    gates_destination: ClassVar[bool] = True
    deploy_actions: ClassVar[list] = ["batch:DescribeJobQueues", "batch:UpdateJobQueue"]

    env_g_id: str
    queue_g_id: str
    order: int = 1

    @property
    def source_g_id(self) -> str:
        return self.env_g_id

    @property
    def destination_g_id(self) -> str:
        return self.queue_g_id

    def read(self, session, G):
        env = G.nodes[self.env_g_id]["data"]
        queue = G.nodes[self.queue_g_id]["data"]
        live = BatchJobQueue.read(session, G, self.queue_g_id, queue.name, region=queue.region)
        if not live:
            return None
        names = {a.split("/")[-1] for a in live.compute_env_arns or []}
        return self if env.name in names else None

    def create(self, session, G):
        """Usually a no-op — the queue read this arrow when it was created.
        Adding an environment to an existing queue is the case that isn't."""
        queue = G.nodes[self.queue_g_id]["data"]
        if self.read(session, G):
            return True
        live = BatchJobQueue.read(session, G, self.queue_g_id, queue.name, region=queue.region)
        if live is None:
            return True  # the queue's own create will include this arrow
        queue.update(session, G)
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        queue = G.nodes[self.queue_g_id]["data"]
        env = G.nodes[self.env_g_id]["data"]
        live = BatchJobQueue.read(session, G, self.queue_g_id, queue.name, region=queue.region)
        if not live:
            return
        remaining = [a for a in live.compute_env_arns or [] if a.split("/")[-1] != env.name]
        if not remaining:
            logger.warning(f"{queue.name} would have no compute environment left; not detaching {env.name}")
            return
        session.client("batch", region_name=queue.region).update_job_queue(
            jobQueue=queue.name,
            computeEnvironmentOrder=[{"order": i + 1, "computeEnvironment": a} for i, a in enumerate(remaining)])


# ============================================================== job definition


class BatchJobDef(BaseNode):
    """The shape of a job: image, size, command, retries. Immutable in AWS —
    every change registers a new revision and the queue runs the latest."""

    deploy_actions: ClassVar[list] = [
        "batch:RegisterJobDefinition", "batch:DescribeJobDefinitions",
        "batch:DeregisterJobDefinition", "batch:TagResource", "iam:PassRole",
        "logs:CreateLogGroup", "logs:DescribeLogGroups", "logs:PutRetentionPolicy", "logs:DeleteLogGroup",
    ]

    name: AwsName
    region: str = "us-east-2"

    image: str
    vcpus: int = 1
    memory_mb: int = 2048
    gpus: Optional[int] = None
    command: List[str] = []
    env: Dict[str, str] = {}
    retry_attempts: int = 1
    timeout_seconds: Optional[int] = None
    platform: str = "ec2"             # ec2 | fargate — must match the queue's environments
    log_retention_days: int = 30
    tags: Dict[str, str] = {}

    # populated by AWS
    arn: Optional[str] = None
    revision: Optional[int] = None
    role_arn: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    @property
    def log_group(self) -> str:
        return f"/batch/{self.name}"

    def _role(self, G):
        for src, _, data in G.in_edges(self.g_id, data=True):
            if isinstance(data["data"], IAMRoleBatchEdge):
                return G.nodes[src]["data"]
        return None

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        batch = session.client("batch", region_name=region)
        defs = batch.describe_job_definitions(jobDefinitionName=read_id, status="ACTIVE")["jobDefinitions"]
        if not defs:
            return None
        jd = max(defs, key=lambda d: d["revision"])
        cp = jd.get("containerProperties") or {}
        reqs = {r["type"]: r["value"] for r in cp.get("resourceRequirements", [])}
        caps = jd.get("platformCapabilities") or ["EC2"]
        return cls(
            g_id=g_id, name=read_id, region=region,
            image=cp.get("image", ""),
            vcpus=int(float(reqs.get("VCPU", cp.get("vcpus", 1)))),
            memory_mb=int(reqs.get("MEMORY", cp.get("memory", 2048))),
            gpus=int(reqs["GPU"]) if "GPU" in reqs else None,
            command=cp.get("command") or [],
            env={e["name"]: e["value"] for e in cp.get("environment", [])},
            retry_attempts=(jd.get("retryStrategy") or {}).get("attempts", 1),
            timeout_seconds=(jd.get("timeout") or {}).get("attemptDurationSeconds"),
            platform="fargate" if "FARGATE" in caps else "ec2",
            tags=_user_tags(jd.get("tags")),
            arn=jd.get("jobDefinitionArn"), revision=jd.get("revision"),
            role_arn=cp.get("jobRoleArn"),
        )

    def _ensure_log_group(self, session):
        logs = session.client("logs", region_name=self.region)
        try:
            logs.create_log_group(logGroupName=self.log_group)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
        try:
            logs.put_retention_policy(logGroupName=self.log_group, retentionInDays=self.log_retention_days)
        except ClientError as e:
            logger.debug(f"Could not set log retention on {self.log_group}: {e}")

    def _register(self, session, G):
        role = self._role(G)
        if not role or not role.arn:
            logger.error(f"{self.name} has no job role — add an `<EcsTaskRole> -> {self.g_id}` arrow "
                         f"so it can pull images and write logs")
            return False
        self._ensure_log_group(session)
        reqs = [{"type": "VCPU", "value": str(self.vcpus)}, {"type": "MEMORY", "value": str(self.memory_mb)}]
        if self.gpus:
            reqs.append({"type": "GPU", "value": str(self.gpus)})
        container = {
            "image": self.image,
            "resourceRequirements": reqs,
            "environment": [{"name": k, "value": v} for k, v in sorted(self.env.items())],
            "jobRoleArn": role.arn,
            "executionRoleArn": role.arn,
            "logConfiguration": {"logDriver": "awslogs", "options": {
                "awslogs-group": self.log_group, "awslogs-region": self.region,
                "awslogs-stream-prefix": self.name}},
        }
        if self.command:
            container["command"] = self.command
        kwargs = dict(
            jobDefinitionName=self.name, type="container", containerProperties=container,
            retryStrategy={"attempts": self.retry_attempts},
            platformCapabilities=["FARGATE" if self.platform == "fargate" else "EC2"],
            tags=_managed_tags(self.tags),
        )
        if self.timeout_seconds:
            kwargs["timeout"] = {"attemptDurationSeconds": self.timeout_seconds}
        resp = session.client("batch", region_name=self.region).register_job_definition(**kwargs)
        self.arn, self.revision, self.role_arn = resp["jobDefinitionArn"], resp["revision"], role.arn
        return True

    def create(self, session, G):
        if not self._register(session, G):
            return False
        logger.info(f"Registered Batch job definition {self.name}:{self.revision} "
                    f"({self.vcpus} vCPU, {self.memory_mb} MB, {self.image})")
        return True

    def update(self, session, G, diff=None):
        if self._register(session, G):
            logger.info(f"Registered new revision {self.name}:{self.revision}")

    def delete(self, session, G):
        batch = session.client("batch", region_name=self.region)
        for jd in batch.describe_job_definitions(jobDefinitionName=self.name, status="ACTIVE")["jobDefinitions"]:
            try:
                batch.deregister_job_definition(jobDefinition=jd["jobDefinitionArn"])
            except ClientError as e:
                logger.warning(f"Could not deregister {jd['jobDefinitionArn']}: {e}")
        logger.info(f"Deregistered Batch job definition {self.name}")
        try:
            session.client("logs", region_name=self.region).delete_log_group(logGroupName=self.log_group)
        except ClientError as e:
            logger.debug(f"Could not delete log group {self.log_group}: {e}")

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        tag = f"batch-job:{self.name}"
        if not live:
            return [VerifyResult(name=tag, passed=False, message="no active job definition")]
        leaked = sorted(k for k, v in (live.env or {}).items() if v and SECRET_NAME.search(k))
        return [
            VerifyResult(name=f"{tag}:has-role", passed=bool(live.role_arn),
                         message=f"runs as {live.role_arn.split('/')[-1]}" if live.role_arn else "no job role"),
            VerifyResult(name=f"{tag}:no-plaintext-secrets", passed=not leaked,
                         message="no secret-looking environment variables" if not leaked
                                 else f"{', '.join(leaked)} set in plain text — anyone who can describe the job "
                                      f"definition can read them; use Secrets Manager / SSM"),
        ]


class IAMRoleBatchEdge(BaseEdge):
    """`job-role -> jobdef`: the role the container runs as, plus the policy
    that lets Batch pull its image from ECR and write its logs — the same
    AmazonECSTaskExecutionRolePolicy a Fargate service needs, because a
    Batch job *is* an ECS task."""

    deploy_actions: ClassVar[list] = [
        "iam:AttachRolePolicy", "iam:DetachRolePolicy", "iam:ListAttachedRolePolicies",
    ]

    role_g_id: str
    jobdef_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.role_g_id

    @property
    def destination_g_id(self) -> str:
        return self.jobdef_g_id

    def _attached(self, session, role_name):
        try:
            attached = session.client("iam").list_attached_role_policies(RoleName=role_name)
        except ClientError:
            return None
        return {p["PolicyArn"] for p in attached.get("AttachedPolicies", [])}

    def read(self, session, G):
        arns = self._attached(session, G.nodes[self.role_g_id]["data"].name)
        return self if arns and TASK_EXECUTION_POLICY in arns else None

    def create(self, session, G):
        role = G.nodes[self.role_g_id]["data"]
        session.client("iam").attach_role_policy(RoleName=role.name, PolicyArn=TASK_EXECUTION_POLICY)
        logger.info(f"Attached task execution policy to {role.name}")
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        role = G.nodes[self.role_g_id]["data"]
        try:
            session.client("iam").detach_role_policy(RoleName=role.name, PolicyArn=TASK_EXECUTION_POLICY)
        except ClientError as e:
            logger.warning(f"Could not detach task execution policy: {e}")


class BatchS3Edge(IAMRoleInlinePolicyEdge):
    """`jobdef -> bucket`: the job may read (and by default write) exactly
    this bucket. An inline policy on the job's role, named after the arrow,
    removed with it. `access: "read"` for reference data and public
    datasets you only ever pull from."""

    deploy_actions: ClassVar[list] = IAMRoleInlinePolicyEdge.deploy_actions

    role_g_id: Optional[str] = None   # found from the jobdef's `role -> jobdef` arrow
    jobdef_g_id: str
    bucket_g_id: str
    access: str = "readwrite"         # read | readwrite

    @property
    def source_g_id(self) -> str:
        return self.jobdef_g_id

    @property
    def destination_g_id(self) -> str:
        return self.bucket_g_id

    def _role_name(self, G) -> Optional[str]:
        role = G.nodes[self.jobdef_g_id]["data"]._role(G)
        return role.name if role else None

    def _policy(self, bucket_name):
        arn = f"arn:aws:s3:::{bucket_name}"
        objects = ["s3:GetObject", "s3:GetObjectVersion"]
        if self.access == "readwrite":
            objects += ["s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"]
        return IamPolicyDocument(Statement=[
            IamPolicyStatement(Sid="ListBucket", Action=["s3:ListBucket", "s3:GetBucketLocation"], Resource=[arn]),
            IamPolicyStatement(Sid="Objects", Action=objects, Resource=[f"{arn}/*"]),
        ])

    def read(self, session, G):
        role_name = self._role_name(G)
        if not role_name:
            return None
        return self if get_inline_policy_for_role(session, role_name, self.policy_name) else None

    def create(self, session, G):
        role_name = self._role_name(G)
        if not role_name:
            logger.error(f"{self.jobdef_g_id} has no job role yet — add `<EcsTaskRole> -> {self.jobdef_g_id}` first")
            return False
        bucket = G.nodes[self.bucket_g_id]["data"]
        put_inline_policy_for_role(session, role_name, self.policy_name, self._policy(bucket.bucket_name))
        logger.info(f"Granted {role_name} {self.access} on s3://{bucket.bucket_name}")
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        role_name = self._role_name(G)
        if not role_name:
            return
        try:
            session.client("iam").delete_role_policy(RoleName=role_name, PolicyName=self.policy_name)
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":
                logger.warning(f"Could not remove {self.policy_name}: {e}")

    def verify(self, session, G) -> list:
        role_name = self._role_name(G)
        bucket = G.nodes[self.bucket_g_id]["data"]
        if not role_name:
            return []
        doc = get_inline_policy_for_role(session, role_name, self.policy_name)
        return [VerifyResult(
            name=f"{self.jobdef_g_id}->{bucket.bucket_name}", passed=doc is not None,
            message=f"{self.access} granted to {role_name}" if doc else "no policy — the job cannot reach the bucket")]
