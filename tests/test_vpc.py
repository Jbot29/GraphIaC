"""The Vpc node against moto: a network of your own, and a network you
already had.

What these tests pin down:

  - the shape one node builds: public + private tiers per AZ, an internet
    gateway, NAT (or not), gateway and interface endpoints, and a default
    security group with every rule stripped
  - nodes join it by attribute reference and are BLOCKED until it exists —
    then land in the right tier, with Fargate following the subnet on
    public IPs
  - a second plan after a run is empty: read() reports what create() made
  - `existing: true` is a reference: IMPORTed, never created, never
    deleted, BLOCKED when it can't be found
  - the guard and verify() read the network independently
  - nat: none -> single -> per-az reconciles in place
  - delete takes everything back, including the elastic IPs
"""

import sqlite3

import boto3
import pytest
from moto import mock_aws

import GraphIaC
from GraphIaC import dsl, guards
from GraphIaC.main import OperationType

REGION = "us-east-2"

NETWORK = """
region = "us-east-2"

net : Vpc(name: "test-net", region: region, cidr: "10.0.0.0/16", az_count: 2,
          nat: "single", endpoints: ["s3", "ecr.api"], tags: {project: "lab"})

? default-sg-empty(net)
"""

STACK = NETWORK + """
lb : ALB(name: "test-lb", region: region, vpc_id: net.vpc_id, subnet_ids: net.public_subnet_ids)
db : RDSPostgres(name: "test-db", region: region, db_name: "app",
                 vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids)

cluster   : EcsCluster(name: "test-cluster", region: region)
task-role : EcsTaskRole(name: "test-task-role")
web : EcsService(name: "test-web", region: region, image: "nginx:latest", container_port: 8000,
                 vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids)

cluster   -> web
task-role -> web
lb        -> web
web       -> db : (role_g_id: task-role)
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
def db(tmp_path):
    return sqlite3.connect(str(tmp_path / "state.db"))


def apply(session, conn, src):
    """parse -> load -> run, as the CLI does. Returns (state, blocked, graph)."""
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


def ec2(session):
    return session.client("ec2", region_name=REGION)


def our_vpc(session):
    vpcs = ec2(session).describe_vpcs(Filters=[{"Name": "tag:Name", "Values": ["test-net"]}])["Vpcs"]
    return vpcs[0] if vpcs else None


def tags(resource):
    return {t["Key"]: t["Value"] for t in resource.get("Tags", [])}


def route_tables(session, vpc_id):
    return ec2(session).describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]


def default_route(rt):
    return next((r for r in rt["Routes"] if r.get("DestinationCidrBlock") == "0.0.0.0/0"), None)


# ------------------------------------------------------------- the network


def test_one_node_builds_the_whole_network(aws, db):
    state, blocked, _ = apply(aws, db, NETWORK)
    assert blocked == []
    vpc = our_vpc(aws)
    assert vpc["CidrBlock"] == "10.0.0.0/16"
    assert tags(vpc)["ManagedBy"] == "GraphIaC"
    assert tags(vpc)["project"] == "lab", "user tags land on the VPC"

    net = state.G.nodes["net"]["data"]
    assert len(net.public_subnet_ids) == 2 and len(net.private_subnet_ids) == 2

    subnets = {s["SubnetId"]: s for s in ec2(aws).describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc["VpcId"]]}])["Subnets"]}
    assert len(subnets) == 4
    assert len({subnets[s]["AvailabilityZone"] for s in net.public_subnet_ids}) == 2, "one public subnet per AZ"
    assert all(subnets[s]["MapPublicIpOnLaunch"] for s in net.public_subnet_ids)
    assert not any(subnets[s]["MapPublicIpOnLaunch"] for s in net.private_subnet_ids)
    assert all(tags(subnets[s])["graphiac:tier"] == "private" for s in net.private_subnet_ids)
    assert all(tags(s)["project"] == "lab" for s in subnets.values()), "user tags land on subnets too"

    # the public tier routes to an internet gateway; the private tier to the NAT
    rts = route_tables(aws, vpc["VpcId"])
    by_name = {tags(rt).get("Name"): rt for rt in rts}
    assert default_route(by_name["test-net-public"])["GatewayId"] == net.igw_id
    assert default_route(by_name["test-net-private"])["NatGatewayId"] == net.nat_gateway_ids[0]
    assert len(net.nat_gateway_ids) == 1

    # S3 is a free gateway endpoint on every route table we made; ecr.api is
    # an interface endpoint living in the private subnets behind its own group
    eps = {e["ServiceName"].split(".", 3)[-1]: e for e in ec2(aws).describe_vpc_endpoints(
        Filters=[{"Name": "vpc-id", "Values": [vpc["VpcId"]]}])["VpcEndpoints"]}
    assert eps["s3"]["VpcEndpointType"] == "Gateway"
    assert set(eps["s3"]["RouteTableIds"]) == {by_name["test-net-public"]["RouteTableId"],
                                               by_name["test-net-private"]["RouteTableId"]}
    assert eps["ecr.api"]["VpcEndpointType"] == "Interface"
    assert set(eps["ecr.api"]["SubnetIds"]) == set(net.private_subnet_ids)

    # CIS 5.3 — the default group permits nothing
    dsg = ec2(aws).describe_security_groups(GroupIds=[net.default_sg_id])["SecurityGroups"][0]
    assert dsg["IpPermissions"] == [] and dsg["IpPermissionsEgress"] == []


def test_second_plan_is_empty(aws, db):
    """read() must describe exactly what create() built, or every plan is an UPDATE."""
    apply(aws, db, NETWORK)
    _, ops, _ = plan_for(aws, db, NETWORK)
    assert ops == [], [(o.operation, getattr(o.obj, "g_id", None)) for o in ops]


def test_verify_and_guard_pass_on_a_fresh_network(aws, db):
    state, _, graph = apply(aws, db, NETWORK)
    results = state.G.nodes["net"]["data"].verify(aws, state.G)
    assert {r.name: r.passed for r in results} == {
        "vpc:test-net:default-sg-empty": True,
        "vpc:test-net:private-subnets-offline": True,
        "vpc:test-net:private-no-auto-public-ip": True,
        "vpc:test-net:s3-endpoint": True,
    }, [r.message for r in results if not r.passed]
    [g] = guards.evaluate(aws, graph)
    assert g.status == "pass", g.message


def test_guard_is_pending_before_and_fails_when_someone_opens_the_default_group(aws, db):
    graph = dsl.parse(NETWORK)["graph"]
    [g] = guards.evaluate(aws, graph)
    assert g.status == "pending"

    state, _, _ = apply(aws, db, NETWORK)
    net = state.G.nodes["net"]["data"]
    ec2(aws).authorize_security_group_ingress(GroupId=net.default_sg_id, IpPermissions=[{
        "IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    [g] = guards.evaluate(aws, graph)
    assert g.status == "fail" and "1 ingress" in g.message


# ---------------------------------------------------------- joining it


def test_nodes_join_by_reference_and_land_in_the_right_tier(aws, db):
    # run 1: only the network can be built; everything referencing it waits
    state, blocked, _ = apply(aws, db, STACK)
    assert {b.g_id for b in blocked} >= {"lb", "db", "web"}
    assert all("net" in b.reason for b in blocked if b.g_id in ("lb", "db", "web"))

    # run 2: the network is ready, so the rest lands
    state, blocked, _ = apply(aws, db, STACK)
    assert blocked == [], blocked
    net = state.G.nodes["net"]["data"]

    elb = aws.client("elbv2", region_name=REGION)
    lb = elb.describe_load_balancers(Names=["test-lb"])["LoadBalancers"][0]
    assert lb["VpcId"] == net.vpc_id
    assert {az["SubnetId"] for az in lb["AvailabilityZones"]} == set(net.public_subnet_ids)

    rds = aws.client("rds", region_name=REGION)
    grp = rds.describe_db_subnet_groups(DBSubnetGroupName="test-db-subnets")["DBSubnetGroups"][0]
    assert {s["SubnetIdentifier"] for s in grp["Subnets"]} == set(net.private_subnet_ids)

    ecs = aws.client("ecs", region_name=REGION)
    svc = ecs.describe_services(cluster="test-cluster", services=["test-web"])["services"][0]
    cfg = svc["networkConfiguration"]["awsvpcConfiguration"]
    assert set(cfg["subnets"]) == set(net.private_subnet_ids)
    assert cfg["assignPublicIp"] == "DISABLED", "Fargate followed the private subnets: no public IP"

    # and nothing wants to change
    _, ops, blocked = plan_for(aws, db, STACK)
    assert blocked == []
    assert ops == [], [(o.operation, getattr(o.obj, "g_id", None)) for o in ops]


def test_fargate_still_gets_a_public_ip_in_the_default_vpc(aws, db):
    """The founding-kit behaviour is unchanged: with no Vpc node and no
    explicit choice, tasks in the default VPC's subnets get public IPs."""
    src = """
    cluster   : EcsCluster(name: "c", region: "us-east-2")
    task-role : EcsTaskRole(name: "r")
    web : EcsService(name: "w", region: "us-east-2", image: "nginx:latest", container_port: 80)
    cluster -> web
    task-role -> web
    """
    apply(aws, db, src)
    svc = aws.client("ecs", region_name=REGION).describe_services(cluster="c", services=["w"])["services"][0]
    assert svc["networkConfiguration"]["awsvpcConfiguration"]["assignPublicIp"] == "ENABLED"


# ------------------------------------------------------ existing networks


def test_existing_default_vpc_is_imported_not_created(aws, db):
    src = """
    net : Vpc(existing: true, region: "us-east-2")
    """
    _, ops, _ = plan_for(aws, db, src)
    assert [o.operation for o in ops] == [OperationType.IMPORT]
    net = ops[0].obj
    assert net.existing and net.vpc_id.startswith("vpc-")
    # a default VPC's default subnets are public by construction
    assert net.public_subnet_ids and net.private_subnet_ids == []

    state, blocked, _ = apply(aws, db, src)
    assert blocked == []
    _, ops, _ = plan_for(aws, db, src)
    assert ops == [], "a reference never UPDATEs"


def test_existing_vpc_by_id_classifies_someone_elses_subnets(aws, db):
    """A network nobody tagged for us: public/private comes from the routes."""
    c = ec2(aws)
    vpc_id = c.create_vpc(CidrBlock="172.16.0.0/16")["Vpc"]["VpcId"]
    pub = c.create_subnet(VpcId=vpc_id, CidrBlock="172.16.0.0/24", AvailabilityZone=f"{REGION}a")["Subnet"]["SubnetId"]
    priv = c.create_subnet(VpcId=vpc_id, CidrBlock="172.16.1.0/24", AvailabilityZone=f"{REGION}b")["Subnet"]["SubnetId"]
    igw = c.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    c.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc_id)
    rt = c.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    c.create_route(RouteTableId=rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)
    c.associate_route_table(RouteTableId=rt, SubnetId=pub)

    src = f"""
    net : Vpc(existing: true, vpc_id: "{vpc_id}", region: "us-east-2")
    """
    state, blocked, _ = apply(aws, db, src)
    assert blocked == []
    net = state.G.nodes["net"]["data"]
    assert net.public_subnet_ids == [pub]
    assert net.private_subnet_ids == [priv]
    assert net.cidr == "172.16.0.0/16"


def test_missing_reference_is_blocked_not_created(aws, db):
    src = """
    net : Vpc(existing: true, vpc_id: "vpc-0123456789abcdef0", region: "us-east-2")
    """
    _, ops, _ = plan_for(aws, db, src)
    assert [o.operation for o in ops] == [OperationType.BLOCKED]
    assert "not found" in ops[0].obj.reason
    assert our_vpc(aws) is None


def test_removing_a_reference_forgets_it_without_deleting(aws, db):
    apply(aws, db, 'net : Vpc(existing: true, region: "us-east-2")')
    default = ec2(aws).describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    assert len(default) == 1

    _, ops, _ = plan_for(aws, db, "# the network left the file")
    deletes = [o for o in ops if o.operation == OperationType.DELETE]
    assert [o.obj.g_id for o in deletes] == ["net"]

    apply(aws, db, "# the network left the file")
    assert ec2(aws).describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"] == default
    _, ops, _ = plan_for(aws, db, "# the network left the file")
    assert ops == [], "the DB row is gone, so nothing is orphaned"


# ------------------------------------------------------- change and teardown


def test_nat_reconciles_none_single_per_az(aws, db):
    def with_nat(mode):
        return NETWORK.replace('nat: "single"', f'nat: "{mode}"')

    def nats(vpc_id):
        return [n for n in ec2(aws).describe_nat_gateways(
            Filter=[{"Name": "vpc-id", "Values": [vpc_id]}])["NatGateways"] if n["State"] == "available"]

    state, _, _ = apply(aws, db, with_nat("none"))
    vpc_id = state.G.nodes["net"]["data"].vpc_id
    assert nats(vpc_id) == []
    private = next(rt for rt in route_tables(aws, vpc_id) if tags(rt).get("Name") == "test-net-private")
    assert default_route(private) is None, "no NAT, no way out — by design"

    _, ops, _ = plan_for(aws, db, with_nat("per-az"))
    assert [o.operation for o in ops] == [OperationType.UPDATE]
    apply(aws, db, with_nat("per-az"))
    assert len(nats(vpc_id)) == 2
    per_az = [rt for rt in route_tables(aws, vpc_id) if (tags(rt).get("Name") or "").startswith("test-net-private-")]
    assert len(per_az) == 2
    assert {default_route(rt)["NatGatewayId"] for rt in per_az} == {n["NatGatewayId"] for n in nats(vpc_id)}

    apply(aws, db, with_nat("none"))
    assert nats(vpc_id) == []
    assert ec2(aws).describe_addresses()["Addresses"] == [], "elastic IPs released with the gateways"
    _, ops, _ = plan_for(aws, db, with_nat("none"))
    assert ops == []


def test_delete_takes_everything_back(aws, db):
    state, _, _ = apply(aws, db, NETWORK)
    vpc_id = state.G.nodes["net"]["data"].vpc_id

    apply(aws, db, "# the network left the file")
    assert our_vpc(aws) is None
    c = ec2(aws)
    assert c.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"] == []
    assert c.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"] == []
    assert [n for n in c.describe_nat_gateways(Filter=[{"Name": "vpc-id", "Values": [vpc_id]}])["NatGateways"]
            if n["State"] == "available"] == []
    assert c.describe_addresses()["Addresses"] == []
    assert [e for e in c.describe_vpc_endpoints(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["VpcEndpoints"]
            if e["State"] not in ("deleted", "deleting")] == []
