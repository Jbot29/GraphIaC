"""The container track, end to end against moto: ALB -> ECS Fargate -> RDS.

The point of these tests is not that the resources get created — it's that
the *edges* wrote the four things nobody wants to write by hand:

  - the target group, and the listener forwarding to it        (alb -> web)
  - the tasks' group admitting the load balancer's group       (alb -> web)
  - the database's group admitting the tasks' group            (web -> db)
  - GetSecretValue on exactly the RDS-managed secret           (web -> db)
"""

import sqlite3

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl, guards

REGION = "us-east-2"

STACK = """
region = "us-east-2"

lb : ALB(name: "test-lb", region: region)
db : RDSPostgres(name: "test-db", region: region, db_name: "app")

cluster   : EcsCluster(name: "test-cluster", region: region)
task-role : EcsTaskRole(name: "test-task-role")
web : EcsService(name: "test-web", region: region,
                 image: "nginx:latest", container_port: 8000,
                 env: {DATABASE_NAME: "app"})

cluster   -> web
task-role -> web
lb        -> web : (health_check_path: "/healthz")
web       -> db : (role_g_id: task-role)

? db-private(db)
"""


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("MOTO_IAM_LOAD_MANAGED_POLICIES", "true")
    with mock_aws():
        yield boto3.session.Session(region_name=REGION)


@pytest.fixture
def stack(aws, tmp_path):
    """The whole graph, applied. Returns (session, state, graph)."""
    state = GraphIaC.init(aws, sqlite3.connect(str(tmp_path / "stack.db")))
    res = dsl.parse(STACK)
    assert res["errors"] == [], res["errors"]
    blocked = dsl.load_graph(state, res["graph"])
    assert blocked == [], blocked
    GraphIaC.run(state, blocked)
    return aws, state, res["graph"]


def sg_named(session, name):
    groups = session.client("ec2", region_name=REGION).describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [name]}]
    )["SecurityGroups"]
    return groups[0] if groups else None


def ingress_sources(group, port):
    """(cidrs, source group ids) allowed to reach `port` on this group."""
    cidrs, groups = set(), set()
    for perm in group.get("IpPermissions", []):
        if perm.get("FromPort") != port or perm.get("ToPort") != port:
            continue
        cidrs.update(r["CidrIp"] for r in perm.get("IpRanges", []))
        groups.update(p["GroupId"] for p in perm.get("UserIdGroupPairs", []))
    return cidrs, groups


def test_load_balancer_and_listener(stack):
    session, _, _ = stack
    elb = session.client("elbv2", region_name=REGION)

    lbs = elb.describe_load_balancers(Names=["test-lb"])["LoadBalancers"]
    assert lbs[0]["Scheme"] == "internet-facing"

    listeners = elb.describe_listeners(LoadBalancerArn=lbs[0]["LoadBalancerArn"])["Listeners"]
    http = next(x for x in listeners if x["Port"] == 80)

    # alb -> web replaced the placeholder 503 with a forward to the target group
    forwards = [a for a in http["DefaultActions"] if a["Type"] == "forward"]
    assert forwards, "listener still returns the placeholder 503"

    groups = elb.describe_target_groups(Names=["test-web-tg"])["TargetGroups"]
    assert groups[0]["TargetType"] == "ip"          # Fargate tasks are ENIs
    assert groups[0]["Port"] == 8000
    assert groups[0]["HealthCheckPath"] == "/healthz"
    assert forwards[0]["TargetGroupArn"] == groups[0]["TargetGroupArn"]


def test_alb_is_the_only_thing_facing_the_internet(stack):
    session, _, _ = stack

    alb_sg = sg_named(session, "test-lb-alb-sg")
    for port in (80, 443):
        cidrs, _ = ingress_sources(alb_sg, port)
        assert "0.0.0.0/0" in cidrs

    # The tasks hold public IPs (no NAT gateway) but admit only the ALB.
    svc_sg = sg_named(session, "test-web-svc-sg")
    cidrs, sources = ingress_sources(svc_sg, 8000)
    assert cidrs == set(), "the container port is open to the internet"
    assert sources == {alb_sg["GroupId"]}


def test_the_database_admits_exactly_the_service(stack):
    """`web -> db` — one arrow, and the most-Googled rule in the pattern."""
    session, _, _ = stack

    db_sg = sg_named(session, "test-db-db-sg")
    svc_sg = sg_named(session, "test-web-svc-sg")

    cidrs, sources = ingress_sources(db_sg, 5432)
    assert cidrs == set(), "Postgres is open to the internet"
    assert sources == {svc_sg["GroupId"]}


def test_service_runs_the_task_definition_behind_the_load_balancer(stack):
    session, _, _ = stack
    ecs = session.client("ecs", region_name=REGION)

    services = ecs.describe_services(cluster="test-cluster", services=["test-web"])["services"]
    assert len(services) == 1
    service = services[0]
    assert service["launchType"] == "FARGATE"
    assert service["loadBalancers"], "service is not registered with the load balancer"
    assert service["loadBalancers"][0]["containerPort"] == 8000

    td = ecs.describe_task_definition(taskDefinition=service["taskDefinition"])["taskDefinition"]
    assert td["networkMode"] == "awsvpc"
    container = td["containerDefinitions"][0]
    assert container["image"] == "nginx:latest"
    assert container["logConfiguration"]["logDriver"] == "awslogs"
    assert {"name": "DATABASE_NAME", "value": "app"} in container["environment"]


def test_task_role_can_pull_images_and_read_the_db_secret(stack):
    session, _, _ = stack
    iam = session.client("iam")

    attached = iam.list_attached_role_policies(RoleName="test-task-role")["AttachedPolicies"]
    assert any("AmazonECSTaskExecutionRolePolicy" in p["PolicyArn"] for p in attached)

    # The credentials grant only exists if RDS handed back a managed secret;
    # moto does not always populate MasterUserSecret, and the SG half of the
    # edge is asserted above either way.
    rds = session.client("rds", region_name=REGION)
    db = rds.describe_db_instances(DBInstanceIdentifier="test-db")["DBInstances"][0]
    secret_arn = (db.get("MasterUserSecret") or {}).get("SecretArn")
    if not secret_arn:
        pytest.skip("moto did not return a MasterUserSecret for this instance")

    names = iam.list_role_policies(RoleName="test-task-role")["PolicyNames"]
    assert names, "no inline policy granting the database secret"
    doc = iam.get_role_policy(RoleName="test-task-role", PolicyName=names[0])["PolicyDocument"]
    statement = doc["Statement"][0]
    assert statement["Action"] == ["secretsmanager:GetSecretValue"]
    assert secret_arn in statement["Resource"]


def test_db_private_guard_passes_then_catches_an_open_port(stack):
    session, _, graph = stack

    results = {r.label: r for r in guards.evaluate(session, graph)}
    assert results["? db-private(db)"].status == "pass"

    # Somebody opens Postgres to the world from the console at 2am.
    db_sg = sg_named(session, "test-db-db-sg")
    session.client("ec2", region_name=REGION).authorize_security_group_ingress(
        GroupId=db_sg["GroupId"],
        IpPermissions=[{"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432,
                        "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}],
    )
    r = guards.evaluate(session, graph)[0]
    assert r.status == "fail"
    assert "0.0.0.0/0" in r.message


def test_db_private_guard_is_pending_before_anything_exists(aws):
    src = 'db : RDSPostgres(name: "nothing-yet", region: "us-east-2")\n? db-private(db)\n'
    res = dsl.parse(src)
    assert res["errors"] == []
    assert guards.evaluate(aws, res["graph"])[0].status == "pending"


def test_service_blocks_until_the_database_is_available(aws, tmp_path):
    """The BLOCKED path: env references db.endpoint, so the service can't be
    planned until RDS reports available."""
    src = STACK.replace('env: {DATABASE_NAME: "app"}', "env: {DATABASE_HOST: db.endpoint}")
    state = GraphIaC.init(aws, sqlite3.connect(str(tmp_path / "blocked.db")))
    res = dsl.parse(src)
    assert res["errors"] == []

    blocked = dsl.load_graph(state, res["graph"])
    # Nothing exists yet, so the service (and everything touching it) is held.
    assert {b.g_id for b in blocked} >= {"web"}
    assert any("db" in b.reason for b in blocked if b.g_id == "web")


def test_second_plan_is_empty(aws, tmp_path):
    """The drift test every new node class needs: apply, then re-plan
    against a fresh graph and the same DB. Anything reported here is a
    field whose read() doesn't round-trip what create() wrote — the bug
    that makes a tool re-apply the same change on every run."""
    db_path = str(tmp_path / "idempotent.db")

    state = GraphIaC.init(aws, sqlite3.connect(db_path))
    res = dsl.parse(STACK)
    assert res["errors"] == []
    GraphIaC.run(state, dsl.load_graph(state, res["graph"]))

    again = GraphIaC.init(aws, sqlite3.connect(db_path))
    res2 = dsl.parse(STACK)
    changes = GraphIaC.plan(again, dsl.load_graph(again, res2["graph"]))
    assert changes == [], [(op, getattr(item, "g_id", item)) for op, item in changes]
