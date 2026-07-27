"""ECS on Fargate — your container, running, without a server to patch.

The node inventory is deliberately shorter than AWS's. A task definition is
not a thing anyone wants to name and reason about separately from the
service that runs it, so `EcsService` owns both: change the image or the
memory and it registers a new revision and rolls the service. What's left is
the cluster (real grouping — web and worker belong together), the service,
and a task execution role.

Three arrows do the wiring:

    cluster   -> app    ClusterServiceEdge — which cluster the service runs in
    task-role -> app    IAMRoleEcsEdge — pull images, write logs
    alb       -> app    AlbEcsEdge — the target group, the listener, and the
                        rule letting the load balancer reach the tasks

`app -> db` lives in aws/rds.py, next to the thing it opens.

The service's own security group starts empty in both directions of
interest: nothing may reach the tasks until `alb -> app` says the load
balancer may, and nothing else is reachable from them except by an arrow.
"""

from typing import ClassVar, Dict, List, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode, VerifyResult

from ..logs import setup_logger
from .ec2.alb import NOTHING_BEHIND_ME, _listener_on
from .ec2.network import (
    NETWORK_ACTIONS,
    delete_security_group,
    ensure_ingress,
    ensure_security_group,
    has_ingress,
    resolve_network,
    revoke_ingress,
)
from .iam_policy import IamTrustPolicyDocument, IamTrustPolicyStatement
from .iam_role import IAMRole
from .types import AwsName

logger = setup_logger()

ECS_TASK_TRUST_POLICY = IamTrustPolicyDocument(
    Statement=[
        IamTrustPolicyStatement(
            Sid="GraphIaCTrustEcsTasks",
            Effect="Allow",
            Principal={"Service": "ecs-tasks.amazonaws.com"},
            Action="sts:AssumeRole",
        )
    ]
)

TASK_EXECUTION_POLICY = (
    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
)


class EcsCluster(BaseNode):
    """A cluster is free and holds no configuration worth arguing about —
    it exists so that `web` and `worker` are visibly the same deployment."""

    deploy_actions: ClassVar[list] = [
        "ecs:CreateCluster",
        "ecs:DescribeClusters",
        "ecs:DeleteCluster",
        "ecs:TagResource",
    ]

    name: AwsName
    region: str = "us-east-2"
    arn: Optional[str] = None
    status: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        ecs = session.client("ecs", region_name=region)
        clusters = ecs.describe_clusters(clusters=[read_id]).get("clusters", [])
        for cluster in clusters:
            if cluster.get("status") != "INACTIVE":
                return cls(g_id=g_id, name=read_id, region=region,
                           arn=cluster.get("clusterArn"), status=cluster.get("status"))
        return None

    def create(self, session, G):
        ecs = session.client("ecs", region_name=self.region)
        resp = ecs.create_cluster(
            clusterName=self.name,
            capacityProviders=["FARGATE", "FARGATE_SPOT"],
            tags=[{"key": "ManagedBy", "value": "GraphIaC"}],
        )
        self.arn = resp["cluster"]["clusterArn"]
        self.status = resp["cluster"]["status"]
        logger.info(f"Created ECS cluster {self.name}")
        return True

    def update(self, session, G, diff=None):
        pass

    def delete(self, session, G):
        ecs = session.client("ecs", region_name=self.region)
        try:
            ecs.delete_cluster(cluster=self.name)
            logger.info(f"Deleted ECS cluster {self.name}")
        except ClientError as e:
            logger.error(f"Could not delete cluster {self.name}: {e}")


class EcsTaskRole(IAMRole):
    """An IAMRole whose trust policy names ECS instead of Lambda.

    A subclass rather than a `trust_policy:` argument because nobody should
    have to write a trust policy document in a config file to run a
    container. The registry records the `isa` chain, so this rides every
    edge IAMRole has.
    """

    deploy_actions: ClassVar[list] = IAMRole.deploy_actions + [
        "iam:AttachRolePolicy",
        "iam:PassRole",
    ]

    def create(self, session, G):
        self.trust_policy = self.trust_policy or ECS_TASK_TRUST_POLICY
        return super().create(session, G)


class EcsService(BaseNode):
    deploy_actions: ClassVar[list] = [
        "ecs:RegisterTaskDefinition",
        "ecs:DeregisterTaskDefinition",
        "ecs:DescribeTaskDefinition",
        "ecs:CreateService",
        "ecs:UpdateService",
        "ecs:DeleteService",
        "ecs:DescribeServices",
        "ecs:TagResource",
        "iam:PassRole",
        "logs:CreateLogGroup",
        "logs:DescribeLogGroups",
        "logs:PutRetentionPolicy",
        "logs:DeleteLogGroup",
    ] + NETWORK_ACTIONS

    name: AwsName
    region: str = "us-east-2"

    image: str
    container_port: int = 8000
    cpu: str = "256"
    memory: str = "512"
    desired_count: int = 1
    env: Dict[str, str] = {}

    # Public subnets + a public IP, so tasks reach ECR and Secrets Manager
    # without a NAT gateway. They are still unreachable from outside: the
    # security group has no ingress until `alb -> app` adds one.
    assign_public_ip: bool = True
    log_retention_days: int = 30

    vpc_id: Optional[str] = None
    subnet_ids: Optional[List[str]] = None

    # populated by AWS
    cluster_name: Optional[str] = None
    task_definition_arn: Optional[str] = None
    service_arn: Optional[str] = None
    security_group_id: Optional[str] = None
    running_count: Optional[int] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    @property
    def sg_name(self) -> str:
        return f"{self.name}-svc-sg"

    @property
    def log_group(self) -> str:
        return f"/ecs/{self.name}"

    # --- graph lookups: the service asks its edges what it needs ----------
    #
    # Same pattern as LambdaZipFile finding its execution role: the edge
    # objects are in the graph before any of them is applied, so a node can
    # read its relationships at create() time without depending on ordering.

    @staticmethod
    def _incoming(G, g_id, edge_cls):
        for src, _, data in G.in_edges(g_id, data=True):
            if isinstance(data["data"], edge_cls):
                return G.nodes[src]["data"], data["data"]
        return None, None

    @staticmethod
    def _outgoing(G, g_id, edge_cls):
        for _, dst, data in G.out_edges(g_id, data=True):
            if isinstance(data["data"], edge_cls):
                return G.nodes[dst]["data"], data["data"]
        return None, None

    def _cluster(self, G):
        cluster, _ = self._incoming(G, self.g_id, ClusterServiceEdge)
        return cluster.name if cluster else self.cluster_name

    def _execution_role_arn(self, G):
        role, _ = self._incoming(G, self.g_id, IAMRoleEcsEdge)
        return role.arn if role else None

    def _container_secrets(self, G):
        """Secrets contributed by outgoing edges — today just the database
        credentials from `app -> db`."""
        from .rds import EcsRdsEdge

        secrets = []
        for _, _, data in G.out_edges(self.g_id, data=True):
            edge = data["data"]
            if isinstance(edge, EcsRdsEdge):
                arn = edge.secret_arn(G)
                if arn:
                    secrets.append({"name": "DATABASE_SECRET", "valueFrom": arn})
        return secrets

    # --- lifecycle --------------------------------------------------------

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        cluster = node._cluster(G) if node else None
        if not cluster:
            return None

        ecs = session.client("ecs", region_name=region)
        try:
            services = ecs.describe_services(cluster=cluster, services=[read_id])["services"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("ClusterNotFoundException",):
                return None
            raise
        live = [s for s in services if s.get("status") != "INACTIVE"]
        if not live:
            return None
        svc = live[0]

        image, port, cpu, memory, env = node.image, node.container_port, node.cpu, node.memory, {}
        try:
            td = ecs.describe_task_definition(
                taskDefinition=svc["taskDefinition"])["taskDefinition"]
            container = td["containerDefinitions"][0]
            image = container.get("image", image)
            mappings = container.get("portMappings") or [{}]
            port = mappings[0].get("containerPort", port)
            cpu, memory = td.get("cpu", cpu), td.get("memory", memory)
            env = {e["name"]: e["value"] for e in container.get("environment", [])}
        except ClientError as e:
            logger.debug(f"Could not read task definition for {read_id}: {e}")

        network = (svc.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {}
        sgs = network.get("securityGroups") or []
        return cls(
            g_id=g_id, name=read_id, region=region,
            image=image, container_port=port, cpu=str(cpu), memory=str(memory), env=env,
            desired_count=svc.get("desiredCount", 1),
            assign_public_ip=network.get("assignPublicIp") == "ENABLED",
            subnet_ids=network.get("subnets"),
            cluster_name=cluster,
            task_definition_arn=svc.get("taskDefinition"),
            service_arn=svc.get("serviceArn"),
            security_group_id=sgs[0] if sgs else None,
            running_count=svc.get("runningCount"),
        )

    def _ensure_log_group(self, session):
        logs = session.client("logs", region_name=self.region)
        try:
            logs.create_log_group(logGroupName=self.log_group)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceAlreadyExistsException":
                raise
        try:
            logs.put_retention_policy(logGroupName=self.log_group,
                                      retentionInDays=self.log_retention_days)
        except ClientError as e:
            logger.debug(f"Could not set log retention on {self.log_group}: {e}")

    def _register_task_definition(self, session, G):
        self._ensure_log_group(session)
        execution_role = self._execution_role_arn(G)
        if not execution_role:
            logger.error(
                f"{self.name} has no task execution role — add an "
                f"`<EcsTaskRole> -> {self.g_id}` arrow so it can pull images and write logs"
            )
            return None

        container = {
            "name": self.name,
            "image": self.image,
            "essential": True,
            "portMappings": [{"containerPort": self.container_port, "protocol": "tcp"}],
            "environment": [{"name": k, "value": v} for k, v in sorted(self.env.items())],
            "logConfiguration": {
                "logDriver": "awslogs",
                "options": {
                    "awslogs-group": self.log_group,
                    "awslogs-region": self.region,
                    "awslogs-stream-prefix": "ecs",
                },
            },
        }
        secrets = self._container_secrets(G)
        if secrets:
            container["secrets"] = secrets

        ecs = session.client("ecs", region_name=self.region)
        resp = ecs.register_task_definition(
            family=self.name,
            requiresCompatibilities=["FARGATE"],
            networkMode="awsvpc",
            cpu=self.cpu,
            memory=self.memory,
            executionRoleArn=execution_role,
            taskRoleArn=execution_role,
            containerDefinitions=[container],
        )
        self.task_definition_arn = resp["taskDefinition"]["taskDefinitionArn"]
        return self.task_definition_arn

    def create(self, session, G):
        cluster = self._cluster(G)
        if not cluster:
            logger.error(f"{self.name} is not in a cluster — add a `<cluster> -> {self.g_id}` arrow")
            return False
        self.cluster_name = cluster

        vpc_id, subnet_ids = resolve_network(session, self.region, self.vpc_id, self.subnet_ids)
        if not vpc_id or not subnet_ids:
            logger.error(f"{self.name}: no usable subnets in {self.region}")
            return False
        self.vpc_id, self.subnet_ids = vpc_id, subnet_ids

        self.security_group_id = ensure_security_group(
            session, self.region, self.sg_name,
            f"GraphIaC: tasks for {self.name}", vpc_id,
        )

        if not self._register_task_definition(session, G):
            return False

        # The target group has to exist before create_service — a service's
        # load balancer attachment is set at creation. AlbEcsEdge owns the
        # code; the service just asks it to run first.
        load_balancers = []
        _, alb_edge = self._incoming(G, self.g_id, AlbEcsEdge)
        if alb_edge:
            tg_arn = alb_edge.ensure_target_group(session, G)
            if tg_arn:
                load_balancers = [{
                    "targetGroupArn": tg_arn,
                    "containerName": self.name,
                    "containerPort": self.container_port,
                }]

        ecs = session.client("ecs", region_name=self.region)
        kwargs = dict(
            cluster=cluster,
            serviceName=self.name,
            taskDefinition=self.task_definition_arn,
            desiredCount=self.desired_count,
            launchType="FARGATE",
            networkConfiguration={"awsvpcConfiguration": {
                "subnets": subnet_ids,
                "securityGroups": [self.security_group_id],
                "assignPublicIp": "ENABLED" if self.assign_public_ip else "DISABLED",
            }},
            tags=[{"key": "ManagedBy", "value": "GraphIaC"}],
        )
        if load_balancers:
            kwargs["loadBalancers"] = load_balancers
            kwargs["healthCheckGracePeriodSeconds"] = 60

        resp = ecs.create_service(**kwargs)
        self.service_arn = resp["service"]["serviceArn"]
        logger.info(f"Created ECS service {self.name} in {cluster} ({self.desired_count} task(s))")
        return True

    def update(self, session, G, diff=None):
        """Any change to the container is a new task definition revision and
        a rolling deploy — ECS replaces tasks one at a time behind the load
        balancer, so this is a normal deploy, not an outage."""
        cluster = self._cluster(G)
        if not cluster:
            return
        if not self._register_task_definition(session, G):
            return
        ecs = session.client("ecs", region_name=self.region)
        ecs.update_service(
            cluster=cluster,
            service=self.name,
            taskDefinition=self.task_definition_arn,
            desiredCount=self.desired_count,
        )
        logger.info(f"Rolling {self.name} onto {self.task_definition_arn}")

    def delete(self, session, G):
        cluster = self._cluster(G) or self.cluster_name
        ecs = session.client("ecs", region_name=self.region)
        if cluster:
            try:
                ecs.update_service(cluster=cluster, service=self.name, desiredCount=0)
                ecs.delete_service(cluster=cluster, service=self.name, force=True)
                logger.info(f"Deleted ECS service {self.name}")
            except ClientError as e:
                logger.error(f"Could not delete service {self.name}: {e}")
        try:
            session.client("logs", region_name=self.region).delete_log_group(
                logGroupName=self.log_group)
        except ClientError as e:
            logger.debug(f"Could not delete log group {self.log_group}: {e}")
        if self.security_group_id:
            delete_security_group(session, self.region, self.security_group_id)

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        if not live:
            return [VerifyResult(name=f"svc:{self.name}", passed=False,
                                 message="service does not exist")]

        results = [VerifyResult(
            name=f"svc:{self.name}:running",
            passed=(live.running_count or 0) >= self.desired_count,
            message=f"{live.running_count}/{self.desired_count} tasks running",
        )]

        # The tasks hold a public IP so they can reach ECR without a NAT
        # gateway. That is only safe while the security group says nothing
        # may reach them except the load balancer.
        if live.security_group_id:
            open_to_world = has_ingress(session, self.region, live.security_group_id,
                                        self.container_port, cidr="0.0.0.0/0")
            results.append(VerifyResult(
                name=f"svc:{self.name}:no-world-ingress", passed=not open_to_world,
                message="CONTAINER PORT OPEN TO 0.0.0.0/0 — the load balancer is being bypassed"
                        if open_to_world else "reachable only from named security groups",
            ))
        return results


class ClusterServiceEdge(BaseEdge):
    """Containment: which cluster this service runs in.

    The edge provisions nothing on its own — EcsService.create() reads it to
    find its cluster. It exists so the relationship is in the graph, the
    diagram, and the plan rather than as a string field.
    """

    deploy_actions: ClassVar[list] = ["ecs:DescribeServices"]

    cluster_g_id: str
    service_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.cluster_g_id

    @property
    def destination_g_id(self) -> str:
        return self.service_g_id

    def read(self, session, G):
        cluster = G.nodes[self.cluster_g_id]["data"]
        service = G.nodes[self.service_g_id]["data"]
        ecs = session.client("ecs", region_name=cluster.region)
        try:
            found = ecs.describe_services(cluster=cluster.name, services=[service.name])["services"]
        except ClientError:
            return None
        return self if any(s.get("status") != "INACTIVE" for s in found) else None

    def create(self, session, G):
        return True  # EcsService.create() did the work when it read this edge

    def update(self, session, G, diff=None):
        pass

    def delete(self, session, G):
        pass


class IAMRoleEcsEdge(BaseEdge):
    """Attaches AmazonECSTaskExecutionRolePolicy — pull the image from ECR,
    write logs to CloudWatch. The ECS equivalent of the basic execution
    policy every Lambda needs, and just as universally required."""

    deploy_actions: ClassVar[list] = [
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:ListAttachedRolePolicies",
    ]

    role_g_id: str
    service_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.role_g_id

    @property
    def destination_g_id(self) -> str:
        return self.service_g_id

    def read(self, session, G):
        role = G.nodes[self.role_g_id]["data"]
        iam = session.client("iam")
        try:
            attached = iam.list_attached_role_policies(RoleName=role.name)
        except ClientError:
            return None
        arns = {p["PolicyArn"] for p in attached.get("AttachedPolicies", [])}
        return self if TASK_EXECUTION_POLICY in arns else None

    def create(self, session, G):
        role = G.nodes[self.role_g_id]["data"]
        session.client("iam").attach_role_policy(
            RoleName=role.name, PolicyArn=TASK_EXECUTION_POLICY)
        logger.info(f"Attached task execution policy to {role.name}")
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        role = G.nodes[self.role_g_id]["data"]
        try:
            session.client("iam").detach_role_policy(
                RoleName=role.name, PolicyArn=TASK_EXECUTION_POLICY)
        except ClientError as e:
            logger.warning(f"Could not detach task execution policy: {e}")


class AlbEcsEdge(BaseEdge):
    """`alb -> app` — the target group, the listener, and the security group
    rule, which AWS makes you build as four separate things.

    The load balancer half and the container half of this relationship are
    described in two different services with two different ID formats, and
    getting either wrong produces a health check that times out with no
    useful error. It is one arrow here.
    """

    deploy_actions: ClassVar[list] = [
        "elasticloadbalancing:CreateTargetGroup",
        "elasticloadbalancing:DescribeTargetGroups",
        "elasticloadbalancing:DeleteTargetGroup",
        "elasticloadbalancing:ModifyTargetGroupAttributes",
        "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:ModifyListener",
        "elasticloadbalancing:DescribeTargetHealth",
    ] + NETWORK_ACTIONS

    alb_g_id: str
    ecs_g_id: str
    health_check_path: str = "/"

    @property
    def source_g_id(self) -> str:
        return self.alb_g_id

    @property
    def destination_g_id(self) -> str:
        return self.ecs_g_id

    def _tg_name(self, service):
        # Target group names cap at 32 characters.
        return f"{service.name}-tg"[:32]

    def _find_target_group(self, session, service):
        elb = session.client("elbv2", region_name=service.region)
        try:
            groups = elb.describe_target_groups(Names=[self._tg_name(service)])["TargetGroups"]
        except ClientError:
            return None
        return groups[0]["TargetGroupArn"] if groups else None

    def ensure_target_group(self, session, G) -> Optional[str]:
        """Find-or-create the target group. Called by EcsService.create()
        before the service exists, and again by this edge's create() — the
        service attachment has to be set at service-creation time, so the
        edge's code has to be runnable early."""
        service = G.nodes[self.ecs_g_id]["data"]
        alb = G.nodes[self.alb_g_id]["data"]

        existing = self._find_target_group(session, service)
        if existing:
            return existing

        vpc_id = service.vpc_id or alb.vpc_id
        if not vpc_id:
            vpc_id, _ = resolve_network(session, service.region)
        if not vpc_id:
            logger.error(f"No VPC for target group {self._tg_name(service)}")
            return None

        elb = session.client("elbv2", region_name=service.region)
        resp = elb.create_target_group(
            Name=self._tg_name(service),
            Protocol="HTTP",
            Port=service.container_port,
            VpcId=vpc_id,
            # Fargate tasks are ENIs, not instances.
            TargetType="ip",
            HealthCheckProtocol="HTTP",
            HealthCheckPath=self.health_check_path,
            HealthCheckIntervalSeconds=30,
            HealthCheckTimeoutSeconds=5,
            HealthyThresholdCount=2,
            UnhealthyThresholdCount=3,
            Matcher={"HttpCode": "200-399"},
        )
        arn = resp["TargetGroups"][0]["TargetGroupArn"]
        logger.info(f"Created target group {self._tg_name(service)} -> :{service.container_port}")
        try:
            elb.modify_target_group_attributes(
                TargetGroupArn=arn,
                # Tasks come and go; 30s beats the 300s default at deploy time.
                Attributes=[{"Key": "deregistration_delay.timeout_seconds", "Value": "30"}],
            )
        except ClientError as e:
            logger.debug(f"Could not set deregistration delay: {e}")
        return arn

    def _front_listener(self, session, alb):
        """HTTPS if a certificate has been attached, otherwise plain HTTP."""
        return (_listener_on(session, alb.region, alb.arn, 443)
                or _listener_on(session, alb.region, alb.arn, 80))

    def read(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        service = G.nodes[self.ecs_g_id]["data"]
        if not alb.arn:
            return None
        tg_arn = self._find_target_group(session, service)
        if not tg_arn:
            return None
        listener = self._front_listener(session, alb)
        if not listener:
            return None
        forwards = any(a.get("TargetGroupArn") == tg_arn
                       for a in listener.get("DefaultActions", []))
        if not forwards:
            return None
        if alb.security_group_id and service.security_group_id:
            if not has_ingress(session, service.region, service.security_group_id,
                               service.container_port, source_sg_id=alb.security_group_id):
                return None
        return self

    def create(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        service = G.nodes[self.ecs_g_id]["data"]
        if not alb.arn:
            logger.warning("ALB not created yet; skipping alb -> app wiring")
            return False

        tg_arn = self.ensure_target_group(session, G)
        if not tg_arn:
            return False

        listener = self._front_listener(session, alb)
        if listener:
            session.client("elbv2", region_name=alb.region).modify_listener(
                ListenerArn=listener["ListenerArn"],
                DefaultActions=[{"Type": "forward", "TargetGroupArn": tg_arn}],
            )
            logger.info(f"{alb.name}:{listener['Port']} now forwards to {service.name}")

        # The half everyone forgets: the tasks' group has to admit the load
        # balancer's group, or every health check times out.
        if alb.security_group_id and service.security_group_id:
            ensure_ingress(
                session, service.region, service.security_group_id, service.container_port,
                source_sg_id=alb.security_group_id,
                description=f"GraphIaC: {alb.name} -> {service.name}",
            )
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        service = G.nodes[self.ecs_g_id]["data"]
        elb = session.client("elbv2", region_name=service.region)

        if alb.arn:
            listener = self._front_listener(session, alb)
            if listener:
                try:
                    elb.modify_listener(ListenerArn=listener["ListenerArn"],
                                        DefaultActions=[NOTHING_BEHIND_ME])
                except ClientError as e:
                    logger.warning(f"Could not reset listener: {e}")

        if alb.security_group_id and service.security_group_id:
            revoke_ingress(session, service.region, service.security_group_id,
                           service.container_port, source_sg_id=alb.security_group_id)

        tg_arn = self._find_target_group(session, service)
        if tg_arn:
            try:
                elb.delete_target_group(TargetGroupArn=tg_arn)
            except ClientError as e:
                logger.warning(f"Could not delete target group: {e}")

    def verify(self, session, G) -> list:
        alb = G.nodes[self.alb_g_id]["data"]
        service = G.nodes[self.ecs_g_id]["data"]
        tg_arn = self._find_target_group(session, service)
        if not tg_arn:
            return [VerifyResult(name=f"{alb.name}->{service.name}", passed=False,
                                 message="no target group")]
        elb = session.client("elbv2", region_name=service.region)
        try:
            health = elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
        except ClientError as e:
            return [VerifyResult(name=f"{alb.name}->{service.name}", passed=False,
                                 message=f"could not read target health: {e}")]
        healthy = [t for t in health if t["TargetHealth"]["State"] == "healthy"]
        return [VerifyResult(
            name=f"{alb.name}->{service.name}", passed=bool(healthy),
            message=f"{len(healthy)}/{len(health)} targets healthy" if health
                    else "no targets registered",
        )]


class EcrRepository(BaseNode):
    """Somewhere to push your image. Fargate can pull public images, but the
    moment the container is yours it needs a private registry, and this is
    the cheapest one that exists."""

    deploy_actions: ClassVar[list] = [
        "ecr:CreateRepository",
        "ecr:DescribeRepositories",
        "ecr:DeleteRepository",
        "ecr:TagResource",
        "ecr:SetRepositoryPolicy",
        "ecr:GetAuthorizationToken",
    ]

    name: str
    region: str = "us-east-2"
    scan_on_push: bool = True
    immutable_tags: bool = False

    uri: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        ecr = session.client("ecr", region_name=region)
        try:
            repos = ecr.describe_repositories(repositoryNames=[read_id])["repositories"]
        except ClientError as e:
            if e.response["Error"]["Code"] == "RepositoryNotFoundException":
                return None
            raise
        repo = repos[0]
        return cls(
            g_id=g_id, name=read_id, region=region,
            scan_on_push=(repo.get("imageScanningConfiguration") or {}).get("scanOnPush", False),
            immutable_tags=repo.get("imageTagMutability") == "IMMUTABLE",
            uri=repo.get("repositoryUri"),
        )

    def create(self, session, G):
        ecr = session.client("ecr", region_name=self.region)
        resp = ecr.create_repository(
            repositoryName=self.name,
            imageScanningConfiguration={"scanOnPush": self.scan_on_push},
            imageTagMutability="IMMUTABLE" if self.immutable_tags else "MUTABLE",
            tags=[{"Key": "ManagedBy", "Value": "GraphIaC"}],
        )
        self.uri = resp["repository"]["repositoryUri"]
        logger.info(f"Created ECR repository {self.uri}")
        return True

    def update(self, session, G, diff=None):
        ecr = session.client("ecr", region_name=self.region)
        ecr.put_image_scanning_configuration(
            repositoryName=self.name,
            imageScanningConfiguration={"scanOnPush": self.scan_on_push},
        )

    def delete(self, session, G):
        ecr = session.client("ecr", region_name=self.region)
        try:
            # force: an empty repository is rarely what you have
            ecr.delete_repository(repositoryName=self.name, force=True)
            logger.info(f"Deleted ECR repository {self.name}")
        except ClientError as e:
            logger.error(f"Could not delete repository {self.name}: {e}")
