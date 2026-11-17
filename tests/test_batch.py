"""AWS Batch against moto: the queue a lab's pipelines submit to.

What these pin down:

  - the compute environment owns the boilerplate: security group, instance
    role + profile, a launch template with the root volume you asked for,
    the service-linked role, tags on the instances
  - `env -> queue` gates the queue until the environment is VALID, so the
    stack comes up over three runs with nothing changing in the file
  - `role -> job` attaches the pull-and-log policy; `job -> bucket` writes an
    inline policy scoped to exactly that bucket
  - a second plan after the stack is up is empty
  - Fargate capacity skips the instance machinery
  - verify flags secrets in plain text; the guard reads the network directly
  - changing the job registers a new revision; teardown takes it all back
"""

import sqlite3

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl, guards
from GraphIaC.main import OperationType

REGION = "us-east-2"

LAB = """
region = "us-east-2"

net : Vpc(name: "lab-net", region: region, az_count: 2)

spot : BatchComputeEnv(name: "lab-spot", region: region, capacity: "spot", max_vcpus: 64,
                       root_volume_gb: 200, vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids,
                       tags: {project: "lab"})
queue : BatchJobQueue(name: "lab-queue", region: region)

job-role : EcsTaskRole(name: "lab-job-role")
data     : S3Bucket("lab-data", region: region)
align    : BatchJobDef(name: "lab-align", region: region, image: "IMAGE",
                       vcpus: 4, memory_mb: 16384, command: ["bwa", "mem"],
                       retry_attempts: 3, timeout_seconds: 7200, env: {REF: "s3://lab-data/ref"})

spot     -> queue
job-role -> align
align    -> data

? batch-private(spot)
""".replace("IMAGE", "quay.io/biocontainers/bwa:0.7.17")


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        yield boto3.session.Session(region_name=REGION)


@pytest.fixture
def db(tmp_path):
    return sqlite3.connect(str(tmp_path / "state.db"))


def apply(session, conn, src):
    state = GraphIaC.init(session, conn)
    res = dsl.parse(src)
    assert res["errors"] == [], res["errors"]
    blocked = dsl.load_graph(state, res["graph"])
    GraphIaC.run(state, blocked)
    return state, blocked, res["graph"]


def plan_for(session, conn, src):
    state = GraphIaC.init(session, conn)
    res = dsl.parse(src)
    assert res["errors"] == [], res["errors"]
    blocked = dsl.load_graph(state, res["graph"])
    return state, GraphIaC.plan(state, blocked), blocked


def batch(session):
    return session.client("batch", region_name=REGION)


def env(session, name="lab-spot"):
    envs = [e for e in batch(session).describe_compute_environments(
        computeEnvironments=[name])["computeEnvironments"] if e["status"] != "DELETED"]
    return envs[0] if envs else None


def queue(session, name="lab-queue"):
    qs = [q for q in batch(session).describe_job_queues(jobQueues=[name])["jobQueues"] if q["status"] != "DELETED"]
    return qs[0] if qs else None


def jobdefs(session, name="lab-align"):
    return batch(session).describe_job_definitions(jobDefinitionName=name, status="ACTIVE")["jobDefinitions"]


@pytest.fixture
def lab(aws, db):
    """Three runs, nothing changing in the file: network, then environment,
    then queue. Returns the final (state, graph)."""
    _, blocked1, _ = apply(aws, db, LAB)
    _, blocked2, _ = apply(aws, db, LAB)
    state, blocked3, graph = apply(aws, db, LAB)
    return state, graph, (blocked1, blocked2, blocked3)


def test_comes_up_over_three_runs_without_touching_the_file(lab):
    _, _, (b1, b2, b3) = lab

    def nodes(blocked):  # edges touching a blocked node are listed too
        return {b.g_id for b in blocked if "→" not in b.g_id}

    assert nodes(b1) == {"spot", "queue"}, "run 1: the environment waits on the network, the queue on the environment"
    assert nodes(b2) == {"queue"}, "run 2: the queue still waits — the environment was read before it existed"
    assert b3 == []


def test_the_environment_owns_its_boilerplate(lab, aws):
    state, _, _ = lab
    net = state.G.nodes["net"]["data"]
    e = env(aws)
    cr = e["computeResources"]
    assert e["status"] == "VALID" and e["state"] == "ENABLED"
    assert cr["type"] == "SPOT" and cr["allocationStrategy"] == "SPOT_PRICE_CAPACITY_OPTIMIZED"
    assert cr["maxvCpus"] == 64 and cr["minvCpus"] == 0 and cr["instanceTypes"] == ["optimal"]
    assert set(cr["subnets"]) == set(net.private_subnet_ids), "instances land in the private tier"
    assert cr["tags"]["project"] == "lab" and cr["tags"]["ManagedBy"] == "GraphIaC", "tags reach the instances"
    assert e["tags"]["project"] == "lab"

    ec2 = aws.client("ec2", region_name=REGION)
    sg = ec2.describe_security_groups(GroupIds=cr["securityGroupIds"])["SecurityGroups"][0]
    assert sg["GroupName"] == "lab-spot-batch-sg" and sg["IpPermissions"] == []

    lt = ec2.describe_launch_template_versions(LaunchTemplateId=cr["launchTemplate"]["launchTemplateId"])
    ebs = lt["LaunchTemplateVersions"][0]["LaunchTemplateData"]["BlockDeviceMappings"][0]["Ebs"]
    assert ebs["VolumeSize"] == 200 and ebs["Encrypted"] is True, "the root volume you asked for, encrypted"

    iam = aws.client("iam")
    profile = iam.get_instance_profile(InstanceProfileName="lab-spot-instance-role")["InstanceProfile"]
    assert cr["instanceRole"] == profile["Arn"]
    attached = {p["PolicyArn"] for p in iam.list_attached_role_policies(RoleName="lab-spot-instance-role")["AttachedPolicies"]}
    assert "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role" in attached


def test_the_arrows_did_the_wiring(lab, aws):
    q = queue(aws)
    assert q["state"] == "ENABLED" and q["priority"] == 10
    assert [o["computeEnvironment"].split("/")[-1] for o in q["computeEnvironmentOrder"]] == ["lab-spot"]

    [jd] = jobdefs(aws)
    cp = jd["containerProperties"]
    reqs = {r["type"]: r["value"] for r in cp["resourceRequirements"]}
    assert reqs == {"VCPU": "4", "MEMORY": "16384"}
    assert cp["command"] == ["bwa", "mem"]
    assert cp["environment"] == [{"name": "REF", "value": "s3://lab-data/ref"}]
    assert cp["jobRoleArn"].endswith("/lab-job-role") and cp["executionRoleArn"] == cp["jobRoleArn"]
    assert cp["logConfiguration"]["options"]["awslogs-group"] == "/batch/lab-align"
    assert jd["retryStrategy"]["attempts"] == 3 and jd["timeout"]["attemptDurationSeconds"] == 7200
    assert jd["platformCapabilities"] == ["EC2"]

    logs = aws.client("logs", region_name=REGION)
    [group] = logs.describe_log_groups(logGroupNamePrefix="/batch/lab-align")["logGroups"]
    assert group["retentionInDays"] == 30

    iam = aws.client("iam")
    attached = {p["PolicyArn"] for p in iam.list_attached_role_policies(RoleName="lab-job-role")["AttachedPolicies"]}
    assert "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" in attached, "role -> align"

    [policy_name] = iam.list_role_policies(RoleName="lab-job-role")["PolicyNames"]
    doc = iam.get_role_policy(RoleName="lab-job-role", PolicyName=policy_name)["PolicyDocument"]
    by_sid = {s["Sid"]: s for s in doc["Statement"]}
    assert by_sid["ListBucket"]["Resource"] == ["arn:aws:s3:::lab-data"]
    assert by_sid["Objects"]["Resource"] == ["arn:aws:s3:::lab-data/*"]
    assert "s3:PutObject" in by_sid["Objects"]["Action"], "align -> data is read/write by default"


def test_second_plan_is_empty(lab, aws, db):
    _, ops, blocked = plan_for(aws, db, LAB)
    assert blocked == []
    assert ops == [], [(o.operation, getattr(o.obj, "g_id", None)) for o in ops]


def test_verify_and_guard(lab, aws):
    state, graph, _ = lab
    names = {}
    for g_id in ("spot", "queue", "align"):
        for r in state.G.nodes[g_id]["data"].verify(aws, state.G):
            names[r.name] = (r.passed, r.message)
    failing = {k: v for k, v in names.items() if not v[0]}
    assert failing == {}, failing
    assert "batch-env:lab-spot:private-subnets" in names
    assert "batch-job:lab-align:no-plaintext-secrets" in names

    [g] = guards.evaluate(aws, graph)
    assert g.status == "pass", g.message

    # someone opens SSH on the instances — the guard reads the group, not our state
    live = env(aws)
    aws.client("ec2", region_name=REGION).authorize_security_group_ingress(
        GroupId=live["computeResources"]["securityGroupIds"][0],
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    [g] = guards.evaluate(aws, graph)
    assert g.status == "fail" and "0.0.0.0/0" in g.message


def test_guard_is_pending_before_the_environment_exists(aws):
    [g] = guards.evaluate(aws, dsl.parse(LAB)["graph"])
    assert g.status == "pending"


def test_secrets_in_plain_text_fail_verify(aws, db):
    src = LAB.replace('env: {REF: "s3://lab-data/ref"}', 'env: {REF: "x", DB_PASSWORD: "hunter2", API_TOKEN: "t"}')
    state, _, _ = apply(aws, db, src)
    results = {r.name: r for r in state.G.nodes["align"]["data"].verify(aws, state.G)}
    r = results["batch-job:lab-align:no-plaintext-secrets"]
    assert not r.passed and "API_TOKEN, DB_PASSWORD" in r.message


def test_fargate_capacity_has_no_instances_to_manage(aws, db):
    src = """
    fg : BatchComputeEnv(name: "lab-fargate", region: "us-east-2", capacity: "fargate-spot", max_vcpus: 16)
    """
    state, blocked, _ = apply(aws, db, src)
    assert blocked == []
    e = env(aws, "lab-fargate")
    cr = e["computeResources"]
    assert cr["type"] == "FARGATE_SPOT" and cr["maxvCpus"] == 16
    assert "instanceRole" not in cr and "launchTemplate" not in cr and "instanceTypes" not in cr
    assert aws.client("ec2", region_name=REGION).describe_launch_templates()["LaunchTemplates"] == []
    _, ops, _ = plan_for(aws, db, src)
    assert ops == []


def test_a_queue_without_an_environment_is_an_error_not_a_crash(aws, db):
    state, blocked, _ = apply(aws, db, 'q : BatchJobQueue(name: "lonely-queue", region: "us-east-2")')
    assert blocked == []
    assert queue(aws, "lonely-queue") is None


def test_changing_the_job_registers_a_new_revision(lab, aws, db):
    changed = LAB.replace("vcpus: 4", "vcpus: 8")
    _, ops, _ = plan_for(aws, db, changed)
    assert [(o.operation, o.obj.g_id) for o in ops] == [(OperationType.UPDATE, "align")]
    apply(aws, db, changed)
    defs = sorted(jobdefs(aws), key=lambda d: d["revision"])
    assert [d["revision"] for d in defs] == [1, 2]
    reqs = {r["type"]: r["value"] for r in defs[-1]["containerProperties"]["resourceRequirements"]}
    assert reqs["VCPU"] == "8"
    _, ops, _ = plan_for(aws, db, changed)
    assert ops == []


def test_teardown_takes_it_all_back(lab, aws, db):
    apply(aws, db, "# everything left the file")
    assert env(aws) is None and queue(aws) is None and jobdefs(aws) == []
    ec2 = aws.client("ec2", region_name=REGION)
    assert ec2.describe_launch_templates()["LaunchTemplates"] == []
    assert ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": ["lab-spot-batch-sg"]}])["SecurityGroups"] == []
    iam = aws.client("iam")
    with pytest.raises(iam.exceptions.NoSuchEntityException):
        iam.get_role(RoleName="lab-spot-instance-role")
    with pytest.raises(iam.exceptions.NoSuchEntityException):
        iam.get_instance_profile(InstanceProfileName="lab-spot-instance-role")
    logs = aws.client("logs", region_name=REGION)
    assert logs.describe_log_groups(logGroupNamePrefix="/batch/lab-align")["logGroups"] == []
