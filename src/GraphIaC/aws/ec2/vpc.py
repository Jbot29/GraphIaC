"""A VPC of your own — the first real network node.

Chapters 1–6 of the founding kit deliberately use the account's default VPC
(see network.py): public subnets, no NAT gateway, security groups doing the
isolating. That is the right answer for a founder's first web app. It stops
being the right answer the moment someone asks a network question — an
auditor ("are the databases in private subnets?"), a batch workload pulling
terabytes from S3 (NAT data charges), a lab holding controlled-access data
(NIST 800-171 wants boundaries you can draw).

One node builds the network that answers those questions:

    net : Vpc(cidr: "10.0.0.0/16", az_count: 2, nat: "none", endpoints: ["s3"])

    public subnets   one per AZ, internet gateway, map-public-ip on
    private subnets  one per AZ, no route to the internet unless `nat`
    endpoints        S3 (and DynamoDB) gateway endpoints are free and attached
                     to every route table; anything else ("ecr.api",
                     "ecr.dkr", "logs", "secretsmanager") is an interface
                     endpoint — ~$7/month per AZ each — in the private
                     subnets, with its own group admitting 443 from the VPC
    default SG       stripped of every rule (CIS 5.3): nothing lands in the
                     default group by accident

Other nodes join it by attribute reference, which is a data dependency the
planner already understands — they stay BLOCKED until the network is ready:

    lb  : ALB(vpc_id: net.vpc_id, subnet_ids: net.public_subnet_ids)
    web : EcsService(vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids, ...)
    db  : RDSPostgres(vpc_id: net.vpc_id, subnet_ids: net.private_subnet_ids)

**`existing: true` makes this a reference, not a resource.** Most companies
already have a network — the campus VPC, the one the previous contractor
built, or just the default one — and the worst habit of IaC tools is
insisting everything be born inside them. A reference node is read, never
created, updated, or deleted; GraphIaC classifies its subnets by their
routes and exposes the same `public_subnet_ids` / `private_subnet_ids` so
the rest of the file doesn't know the difference:

    net : Vpc(existing: true, vpc_id: "vpc-0abc...")   # a VPC you already have
    net : Vpc(existing: true)                           # the account's default VPC

Everything the node creates carries `ManagedBy=GraphIaC`, `graphiac:vpc=<name>`
and your `tags` — compliance tooling (Vanta, Drata, Config rules) keys off
tags, so they are first-class here rather than an afterthought.

What this node does NOT do yet: flow logs (compliance will want them — a
follow-up that needs a log group and a role), IPv6, VPN/peering/Transit
Gateway. A NAT gateway is ~$32/month plus data; `nat: "none"` is the
default because with the S3 endpoint most private workloads (Batch pulling
from S3, RDS, FSx) need no internet at all, and the ones that do (ECR pulls
for Fargate) are cheaper through interface endpoints only at very low AZ
counts — the README does that arithmetic.
"""

import ipaddress
from typing import ClassVar, Dict, List, Optional

from botocore.exceptions import ClientError
from pydantic import field_validator

from GraphIaC.models import BaseNode, VerifyResult

from ...logs import setup_logger
from ..types import AwsName
from .network import default_vpc

logger = setup_logger()

MANAGED_TAG = {"Key": "ManagedBy", "Value": "GraphIaC"}
VPC_TAG = "graphiac:vpc"
TIER_TAG = "graphiac:tier"

# Gateway endpoints are free and attach to route tables; everything else is
# an interface endpoint (an ENI per subnet, billed hourly).
GATEWAY_SERVICES = {"s3", "dynamodb"}

NAT_MODES = ("none", "single", "per-az")


def _tags(name: str, extra: Dict[str, str], **more) -> List[dict]:
    tags = [{"Key": "Name", "Value": name}, MANAGED_TAG]
    tags += [{"Key": k, "Value": v} for k, v in sorted({**extra, **more}.items())]
    return tags


def _tag_spec(resource: str, name: str, extra: Dict[str, str], **more) -> List[dict]:
    return [{"ResourceType": resource, "Tags": _tags(name, extra, **more)}]


def _tag_map(resource: dict) -> Dict[str, str]:
    return {t["Key"]: t["Value"] for t in resource.get("Tags", [])}


def _user_tags(resource: dict) -> Dict[str, str]:
    """A resource's tags minus the ones this node writes for itself."""
    return {k: v for k, v in _tag_map(resource).items()
            if k not in ("Name", "ManagedBy") and not k.startswith("graphiac:")}


def _short_service(service_name: str) -> str:
    """'com.amazonaws.us-east-2.ecr.api' -> 'ecr.api'."""
    parts = service_name.split(".")
    return ".".join(parts[3:]) if len(parts) > 3 else service_name


def _has_igw_route(route_table: dict) -> bool:
    return any(
        r.get("DestinationCidrBlock") == "0.0.0.0/0" and str(r.get("GatewayId", "")).startswith("igw-")
        for r in route_table.get("Routes", [])
    )


class Vpc(BaseNode):
    deploy_actions: ClassVar[list] = [
        "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:DescribeVpcs", "ec2:ModifyVpcAttribute",
        "ec2:DescribeVpcAttribute",
        "ec2:CreateSubnet", "ec2:DeleteSubnet", "ec2:DescribeSubnets", "ec2:ModifySubnetAttribute",
        "ec2:DescribeAvailabilityZones",
        "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway", "ec2:AttachInternetGateway",
        "ec2:DetachInternetGateway", "ec2:DescribeInternetGateways",
        "ec2:CreateRouteTable", "ec2:DeleteRouteTable", "ec2:DescribeRouteTables",
        "ec2:CreateRoute", "ec2:DeleteRoute", "ec2:AssociateRouteTable", "ec2:DisassociateRouteTable",
        "ec2:ReplaceRouteTableAssociation",
        "ec2:CreateNatGateway", "ec2:DeleteNatGateway", "ec2:DescribeNatGateways",
        "ec2:AllocateAddress", "ec2:ReleaseAddress", "ec2:DescribeAddresses",
        "ec2:CreateVpcEndpoint", "ec2:DeleteVpcEndpoints", "ec2:DescribeVpcEndpoints",
        "ec2:ModifyVpcEndpoint",
        "ec2:DescribeSecurityGroups", "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
        "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
        "ec2:RevokeSecurityGroupEgress",
        "ec2:CreateTags", "ec2:DescribeTags",
    ]

    name: AwsName
    region: str = "us-east-2"

    # What to build. Ignored when existing=True.
    cidr: str = "10.0.0.0/16"
    az_count: int = 2
    public_subnets: bool = True
    private_subnets: bool = True
    nat: str = "none"                 # none | single | per-az
    endpoints: List[str] = ["s3"]     # gateway (s3, dynamodb) or interface ("ecr.api", "logs", …)
    tags: Dict[str, str] = {}

    # Reference an existing network instead of building one. With vpc_id
    # unset this means the account's default VPC. Never created, updated,
    # or deleted by GraphIaC.
    existing: bool = False

    # populated by AWS
    vpc_id: Optional[str] = None
    public_subnet_ids: Optional[List[str]] = None
    private_subnet_ids: Optional[List[str]] = None
    igw_id: Optional[str] = None
    nat_gateway_ids: Optional[List[str]] = None
    endpoint_ids: Optional[Dict[str, str]] = None
    default_sg_id: Optional[str] = None
    state: Optional[str] = None

    @field_validator("endpoints")
    @classmethod
    def _sorted_endpoints(cls, v):
        return sorted(set(v or []))

    @field_validator("nat")
    @classmethod
    def _known_nat(cls, v):
        if v not in NAT_MODES:
            raise ValueError(f"nat must be one of {', '.join(NAT_MODES)}")
        return v

    @property
    def read_id(self) -> Optional[str]:
        return self.vpc_id if self.existing else self.name

    def is_reference(self) -> bool:
        return self.existing

    def ready(self) -> bool:
        """Subnets are usable the moment they exist; a NAT gateway takes a
        minute or two. Dependents in private subnets wait for it, so the
        first task placed there can actually pull its image."""
        return self.state == "available"

    # ------------------------------------------------------------------ read

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        ec2 = session.client("ec2", region_name=region)
        existing = bool(getattr(node, "existing", False)) or (read_id or "").startswith("vpc-")

        if existing:
            vpc_id = read_id if (read_id or "").startswith("vpc-") else default_vpc(session, region)
            if not vpc_id:
                return None
            try:
                vpcs = ec2.describe_vpcs(VpcIds=[vpc_id])["Vpcs"]
            except ClientError as e:
                if e.response["Error"]["Code"].startswith("InvalidVpcID"):
                    return None
                raise
        else:
            vpcs = ec2.describe_vpcs(Filters=[
                {"Name": "tag:Name", "Values": [read_id]},
                {"Name": "tag:ManagedBy", "Values": ["GraphIaC"]},
            ])["Vpcs"]
        if not vpcs:
            return None
        vpc = vpcs[0]
        vpc_id = vpc["VpcId"]

        subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]
        route_tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]
        public, private = cls._classify_subnets(subnets, route_tables, by_tag=not existing)

        igws = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"]
        nats = [n for n in ec2.describe_nat_gateways(
            Filter=[{"Name": "vpc-id", "Values": [vpc_id]}])["NatGateways"]
            if n.get("State") not in ("deleted", "deleting", "failed")]
        eps = [e for e in ec2.describe_vpc_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["VpcEndpoints"]
            if e.get("State") not in ("deleted", "deleting", "failed", "rejected")]
        default_sg = cls._default_sg(ec2, vpc_id)

        nat_mode = "none"
        if nats:
            want = getattr(node, "nat", None)
            nat_mode = "per-az" if (len(nats) > 1 or (want == "per-az" and len(nats) == len(private))) else "single"

        all_available = vpc.get("State") == "available" and all(n.get("State") == "available" for n in nats)
        name = _tag_map(vpc).get("Name") or getattr(node, "name", None) or vpc_id

        return cls(
            g_id=g_id,
            name=name,
            region=region,
            cidr=vpc.get("CidrBlock", "10.0.0.0/16"),
            az_count=max(len(public), len(private)) or getattr(node, "az_count", 2),
            public_subnets=bool(public),
            private_subnets=bool(private),
            nat=nat_mode,
            endpoints=[_short_service(e["ServiceName"]) for e in eps],
            tags=_user_tags(vpc),
            existing=existing,
            vpc_id=vpc_id,
            public_subnet_ids=public,
            private_subnet_ids=private,
            igw_id=igws[0]["InternetGatewayId"] if igws else None,
            nat_gateway_ids=[n["NatGatewayId"] for n in nats],
            endpoint_ids={_short_service(e["ServiceName"]): e["VpcEndpointId"] for e in eps},
            default_sg_id=default_sg["GroupId"] if default_sg else None,
            state="available" if all_available else "pending",
        )

    @staticmethod
    def _classify_subnets(subnets, route_tables, by_tag):
        """(public ids, private ids), each in stable AZ order.

        Our own subnets carry a tier tag. Someone else's are classified the
        honest way: a subnet is public if the route table it uses has a
        default route to an internet gateway."""
        main_rt = next((rt for rt in route_tables
                        if any(a.get("Main") for a in rt.get("Associations", []))), None)
        rt_for = {}
        for rt in route_tables:
            for a in rt.get("Associations", []):
                if a.get("SubnetId"):
                    rt_for[a["SubnetId"]] = rt

        public, private = [], []
        for s in sorted(subnets, key=lambda s: (s["AvailabilityZone"], s["SubnetId"])):
            tier = _tag_map(s).get(TIER_TAG) if by_tag else None
            if tier is None:
                rt = rt_for.get(s["SubnetId"], main_rt)
                # a default VPC's default subnets are public by construction
                is_public = s.get("DefaultForAz") or (rt is not None and _has_igw_route(rt))
                tier = "public" if is_public else "private"
            (public if tier == "public" else private).append(s["SubnetId"])
        return public, private

    @staticmethod
    def _default_sg(ec2, vpc_id):
        groups = ec2.describe_security_groups(Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "group-name", "Values": ["default"]},
        ])["SecurityGroups"]
        return groups[0] if groups else None

    # ---------------------------------------------------------------- create

    def _plan_cidrs(self):
        """Public subnets from the first half of the block, private from the
        second, each a /20 of a /16 (four bits narrower than the VPC)."""
        net = ipaddress.ip_network(self.cidr)
        subs = list(net.subnets(prefixlen_diff=4))
        half = len(subs) // 2
        if self.az_count > half:
            raise ValueError(f"az_count {self.az_count} needs more subnets than {self.cidr} can carve")
        return [str(s) for s in subs[:self.az_count]], [str(s) for s in subs[half:half + self.az_count]]

    def _azs(self, ec2):
        zones = ec2.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}])["AvailabilityZones"]
        names = sorted(z["ZoneName"] for z in zones if z.get("ZoneType", "availability-zone") == "availability-zone")
        if len(names) < self.az_count:
            raise ValueError(f"{self.region} has {len(names)} availability zones, az_count is {self.az_count}")
        return names[:self.az_count]

    def _find_by_name(self, ec2, describe, key, vpc_id, name):
        resp = describe(Filters=[
            {"Name": "vpc-id", "Values": [vpc_id]},
            {"Name": "tag:Name", "Values": [name]},
        ])
        items = resp[key]
        return items[0] if items else None

    def _ensure_vpc(self, ec2):
        found = ec2.describe_vpcs(Filters=[
            {"Name": "tag:Name", "Values": [self.name]},
            {"Name": "tag:ManagedBy", "Values": ["GraphIaC"]},
        ])["Vpcs"]
        if found:
            self.vpc_id = found[0]["VpcId"]
            return
        resp = ec2.create_vpc(
            CidrBlock=self.cidr,
            TagSpecifications=_tag_spec("vpc", self.name, self.tags, **{VPC_TAG: self.name}),
        )
        self.vpc_id = resp["Vpc"]["VpcId"]
        ec2.get_waiter("vpc_available").wait(VpcIds=[self.vpc_id])
        ec2.modify_vpc_attribute(VpcId=self.vpc_id, EnableDnsSupport={"Value": True})
        ec2.modify_vpc_attribute(VpcId=self.vpc_id, EnableDnsHostnames={"Value": True})
        logger.info(f"Created VPC {self.name} ({self.vpc_id}, {self.cidr})")

    def _ensure_subnet(self, ec2, tier, az, cidr):
        suffix = az[-1]
        sub_name = f"{self.name}-{tier}-{suffix}"
        found = self._find_by_name(ec2, ec2.describe_subnets, "Subnets", self.vpc_id, sub_name)
        if found:
            return found["SubnetId"]
        resp = ec2.create_subnet(
            VpcId=self.vpc_id, CidrBlock=cidr, AvailabilityZone=az,
            TagSpecifications=_tag_spec("subnet", sub_name, self.tags, **{VPC_TAG: self.name, TIER_TAG: tier}),
        )
        subnet_id = resp["Subnet"]["SubnetId"]
        ec2.modify_subnet_attribute(SubnetId=subnet_id, MapPublicIpOnLaunch={"Value": tier == "public"})
        logger.info(f"Created {tier} subnet {sub_name} ({subnet_id}, {cidr})")
        return subnet_id

    def _ensure_igw(self, ec2):
        attached = ec2.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [self.vpc_id]}])["InternetGateways"]
        if attached:
            self.igw_id = attached[0]["InternetGatewayId"]
            return
        resp = ec2.create_internet_gateway(
            TagSpecifications=_tag_spec("internet-gateway", f"{self.name}-igw", self.tags, **{VPC_TAG: self.name}))
        self.igw_id = resp["InternetGateway"]["InternetGatewayId"]
        ec2.attach_internet_gateway(InternetGatewayId=self.igw_id, VpcId=self.vpc_id)
        logger.info(f"Created internet gateway {self.igw_id}")

    def _ensure_route_table(self, ec2, rt_name, subnet_ids):
        found = self._find_by_name(ec2, ec2.describe_route_tables, "RouteTables", self.vpc_id, rt_name)
        if found:
            rt_id = found["RouteTableId"]
        else:
            rt_id = ec2.create_route_table(
                VpcId=self.vpc_id,
                TagSpecifications=_tag_spec("route-table", rt_name, self.tags, **{VPC_TAG: self.name}),
            )["RouteTable"]["RouteTableId"]
            logger.info(f"Created route table {rt_name} ({rt_id})")
        # A subnet has exactly one route table. Move it here if it is
        # somewhere else (nat: single -> per-az splits one table into many).
        current = {}
        for rt in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [self.vpc_id]}])["RouteTables"]:
            for a in rt.get("Associations", []):
                if a.get("SubnetId"):
                    current[a["SubnetId"]] = (rt["RouteTableId"], a["RouteTableAssociationId"])
        for subnet_id in subnet_ids:
            have = current.get(subnet_id)
            if have is None:
                ec2.associate_route_table(RouteTableId=rt_id, SubnetId=subnet_id)
            elif have[0] != rt_id:
                ec2.replace_route_table_association(AssociationId=have[1], RouteTableId=rt_id)
        return rt_id

    def _drop_unused_route_tables(self, ec2, keep):
        """Tables this node made that no subnet uses any more (per-az -> single)."""
        for rt in ec2.describe_route_tables(Filters=[
                {"Name": "vpc-id", "Values": [self.vpc_id]},
                {"Name": f"tag:{VPC_TAG}", "Values": [self.name]}])["RouteTables"]:
            if rt["RouteTableId"] in keep or rt.get("Associations"):
                continue
            ec2.delete_route_table(RouteTableId=rt["RouteTableId"])
            logger.info(f"Deleted unused route table {_tag_map(rt).get('Name')} ({rt['RouteTableId']})")

    def _set_default_route(self, ec2, rt_id, **target):
        """Point 0.0.0.0/0 at an internet or NAT gateway, replacing whatever
        it pointed at before; with no target, remove the default route."""
        rt = ec2.describe_route_tables(RouteTableIds=[rt_id])["RouteTables"][0]
        current = next((r for r in rt["Routes"] if r.get("DestinationCidrBlock") == "0.0.0.0/0"), None)
        wanted = target.get("GatewayId") or target.get("NatGatewayId")
        have = (current or {}).get("GatewayId") or (current or {}).get("NatGatewayId")
        if have == wanted:
            return
        if current:
            ec2.delete_route(RouteTableId=rt_id, DestinationCidrBlock="0.0.0.0/0")
        if wanted:
            ec2.create_route(RouteTableId=rt_id, DestinationCidrBlock="0.0.0.0/0", **target)

    def _live_nats(self, ec2):
        return [n for n in ec2.describe_nat_gateways(
            Filter=[{"Name": "vpc-id", "Values": [self.vpc_id]}])["NatGateways"]
            if n.get("State") not in ("deleted", "deleting", "failed")]

    def _ensure_nats(self, ec2, public_ids):
        """Reconcile NAT gateways to `self.nat`: 0, 1, or one per AZ."""
        want = {"none": 0, "single": 1, "per-az": len(public_ids)}[self.nat]
        if want and not public_ids:
            raise ValueError(f"{self.name}: nat={self.nat!r} needs public subnets to live in")
        live = self._live_nats(ec2)
        by_subnet = {n["SubnetId"]: n for n in live}
        keep = []
        for subnet_id in public_ids[:want]:
            if subnet_id in by_subnet:
                keep.append(by_subnet.pop(subnet_id))
                continue
            eip = ec2.allocate_address(
                Domain="vpc",
                TagSpecifications=_tag_spec("elastic-ip", f"{self.name}-nat-eip", self.tags, **{VPC_TAG: self.name}))
            resp = ec2.create_nat_gateway(
                SubnetId=subnet_id, AllocationId=eip["AllocationId"],
                TagSpecifications=_tag_spec("natgateway", f"{self.name}-nat", self.tags, **{VPC_TAG: self.name}))
            keep.append(resp["NatGateway"])
            logger.info(f"Creating NAT gateway {resp['NatGateway']['NatGatewayId']} in {subnet_id} "
                        f"(~$32/month + data; takes a minute or two)")
        for extra in by_subnet.values():  # more than wanted — e.g. per-az -> single
            self._delete_nat(ec2, extra)
        self.nat_gateway_ids = [n["NatGatewayId"] for n in keep]
        return keep

    def _delete_nat(self, ec2, nat):
        ec2.delete_nat_gateway(NatGatewayId=nat["NatGatewayId"])
        logger.info(f"Deleting NAT gateway {nat['NatGatewayId']}")
        try:
            ec2.get_waiter("nat_gateway_deleted").wait(
                NatGatewayIds=[nat["NatGatewayId"]], WaiterConfig={"Delay": 10, "MaxAttempts": 30})
        except Exception as e:  # moto has no such waiter; AWS may time out — the EIP release below tells us
            logger.debug(f"nat_gateway_deleted waiter: {e}")
        for addr in nat.get("NatGatewayAddresses", []):
            if addr.get("AllocationId"):
                try:
                    ec2.release_address(AllocationId=addr["AllocationId"])
                except ClientError as e:
                    logger.warning(f"Could not release {addr['AllocationId']} yet: {e.response['Error']['Code']}")

    def _ensure_endpoints(self, ec2, route_table_ids, interface_subnet_ids):
        live = {_short_service(e["ServiceName"]): e for e in ec2.describe_vpc_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [self.vpc_id]}])["VpcEndpoints"]
            if e.get("State") not in ("deleted", "deleting", "failed", "rejected")}
        self.endpoint_ids = {}
        for short in self.endpoints:
            service = f"com.amazonaws.{self.region}.{short}"
            if short in live:
                ep = live.pop(short)
                self.endpoint_ids[short] = ep["VpcEndpointId"]
                if short in GATEWAY_SERVICES:
                    missing = sorted(set(route_table_ids) - set(ep.get("RouteTableIds", [])))
                    if missing:
                        ec2.modify_vpc_endpoint(VpcEndpointId=ep["VpcEndpointId"], AddRouteTableIds=missing)
                continue
            kwargs = dict(
                VpcId=self.vpc_id, ServiceName=service,
                TagSpecifications=_tag_spec("vpc-endpoint", f"{self.name}-{short}", self.tags, **{VPC_TAG: self.name}),
            )
            if short in GATEWAY_SERVICES:
                kwargs.update(VpcEndpointType="Gateway", RouteTableIds=route_table_ids)
            else:
                if not interface_subnet_ids:
                    raise ValueError(f"{self.name}: interface endpoint {short} needs subnets")
                kwargs.update(
                    VpcEndpointType="Interface", SubnetIds=interface_subnet_ids,
                    SecurityGroupIds=[self._ensure_endpoint_sg(ec2)], PrivateDnsEnabled=True,
                )
            ep = ec2.create_vpc_endpoint(**kwargs)["VpcEndpoint"]
            self.endpoint_ids[short] = ep["VpcEndpointId"]
            logger.info(f"Created {kwargs['VpcEndpointType'].lower()} endpoint for {short} ({ep['VpcEndpointId']})")
        for short, ep in live.items():  # endpoints removed from the file
            ec2.delete_vpc_endpoints(VpcEndpointIds=[ep["VpcEndpointId"]])
            logger.info(f"Deleted endpoint for {short} ({ep['VpcEndpointId']})")

    @property
    def endpoint_sg_name(self) -> str:
        return f"{self.name}-endpoints-sg"

    def _ensure_endpoint_sg(self, ec2):
        """One group for all interface endpoints: 443 from inside the VPC."""
        groups = ec2.describe_security_groups(Filters=[
            {"Name": "vpc-id", "Values": [self.vpc_id]},
            {"Name": "group-name", "Values": [self.endpoint_sg_name]},
        ])["SecurityGroups"]
        if groups:
            return groups[0]["GroupId"]
        sg_id = ec2.create_security_group(
            GroupName=self.endpoint_sg_name, Description=f"GraphIaC: interface endpoints for {self.name}",
            VpcId=self.vpc_id,
            TagSpecifications=_tag_spec("security-group", self.endpoint_sg_name, self.tags, **{VPC_TAG: self.name}),
        )["GroupId"]
        ec2.authorize_security_group_ingress(GroupId=sg_id, IpPermissions=[{
            "IpProtocol": "tcp", "FromPort": 443, "ToPort": 443,
            "IpRanges": [{"CidrIp": self.cidr, "Description": "AWS APIs from inside the VPC"}],
        }])
        return sg_id

    def _lock_default_sg(self, ec2):
        """CIS 5.3: the default group permits nothing. Anything that lands in
        it by omission is then unreachable rather than open."""
        sg = self._default_sg(ec2, self.vpc_id)
        if not sg:
            return
        self.default_sg_id = sg["GroupId"]
        if sg.get("IpPermissions"):
            ec2.revoke_security_group_ingress(GroupId=sg["GroupId"], IpPermissions=sg["IpPermissions"])
        if sg.get("IpPermissionsEgress"):
            ec2.revoke_security_group_egress(GroupId=sg["GroupId"], IpPermissions=sg["IpPermissionsEgress"])

    def _ensure_all(self, session):
        """Find-or-create every piece. Both create() and update() are this —
        a partial run leaves tagged resources behind and the next run
        finishes the job instead of duplicating it."""
        ec2 = session.client("ec2", region_name=self.region)
        self._ensure_vpc(ec2)
        azs = self._azs(ec2)
        public_cidrs, private_cidrs = self._plan_cidrs()

        route_tables = []
        self.public_subnet_ids, self.private_subnet_ids = [], []
        if self.public_subnets:
            self.public_subnet_ids = [self._ensure_subnet(ec2, "public", az, c) for az, c in zip(azs, public_cidrs)]
            self._ensure_igw(ec2)
            rt = self._ensure_route_table(ec2, f"{self.name}-public", self.public_subnet_ids)
            self._set_default_route(ec2, rt, GatewayId=self.igw_id)
            route_tables.append(rt)

        if self.private_subnets:
            self.private_subnet_ids = [self._ensure_subnet(ec2, "private", az, c) for az, c in zip(azs, private_cidrs)]
            nats = self._ensure_nats(ec2, self.public_subnet_ids or [])  # reconciles to 0, 1, or per-AZ
            if self.nat == "per-az":
                for az, subnet_id, nat in zip(azs, self.private_subnet_ids, nats):
                    rt = self._ensure_route_table(ec2, f"{self.name}-private-{az[-1]}", [subnet_id])
                    self._set_default_route(ec2, rt, NatGatewayId=nat["NatGatewayId"])
                    route_tables.append(rt)
            else:
                rt = self._ensure_route_table(ec2, f"{self.name}-private", self.private_subnet_ids)
                if nats:
                    self._set_default_route(ec2, rt, NatGatewayId=nats[0]["NatGatewayId"])
                else:
                    self._set_default_route(ec2, rt)
                route_tables.append(rt)
        else:
            self.nat_gateway_ids = []

        self._drop_unused_route_tables(ec2, keep=set(route_tables))
        self._ensure_endpoints(ec2, route_tables, self.private_subnet_ids or self.public_subnet_ids)
        self._lock_default_sg(ec2)
        self.state = "available" if all(
            n.get("State") == "available" for n in self._live_nats(ec2)) else "pending"
        return True

    def create(self, session, G) -> bool:
        if self.existing:
            logger.error(f"{self.name} is a reference to an existing VPC — nothing to create")
            return False
        return self._ensure_all(session)

    def update(self, session, G, diff=None):
        if self.existing:
            return
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        if live and live.cidr != self.cidr:
            logger.error(f"{self.name}: a VPC's CIDR cannot be changed in place "
                         f"({live.cidr} -> {self.cidr}); rename the node to build a new one")
            self.cidr = live.cidr
        if live and live.az_count > self.az_count:
            logger.warning(f"{self.name}: not deleting subnets to shrink az_count "
                           f"{live.az_count} -> {self.az_count}; remove them by hand if you mean it")
            self.az_count = live.az_count
        self._ensure_all(session)

    # ---------------------------------------------------------------- delete

    def delete(self, session, G):
        if self.existing:
            logger.info(f"{self.name} is a reference — forgetting it, not deleting the VPC")
            return
        ec2 = session.client("ec2", region_name=self.region)
        vpc_id = self.vpc_id
        if not vpc_id:
            live = self.read(session, G, self.g_id, self.name, region=self.region)
            if not live:
                return
            vpc_id = live.vpc_id
        self.vpc_id = vpc_id
        flt = [{"Name": "vpc-id", "Values": [vpc_id]}]

        def attempt(what, fn):
            try:
                fn()
            except ClientError as e:
                logger.warning(f"Could not {what}: {e.response['Error']['Code']} — {e.response['Error'].get('Message', '')}")

        eps = [e["VpcEndpointId"] for e in ec2.describe_vpc_endpoints(Filters=flt)["VpcEndpoints"]
               if e.get("State") not in ("deleted", "deleting")]
        if eps:
            attempt("delete endpoints", lambda: ec2.delete_vpc_endpoints(VpcEndpointIds=eps))

        for nat in self._live_nats(ec2):
            attempt(f"delete NAT {nat['NatGatewayId']}", lambda n=nat: self._delete_nat(ec2, n))

        for rt in ec2.describe_route_tables(Filters=flt)["RouteTables"]:
            if any(a.get("Main") for a in rt.get("Associations", [])):
                continue
            for a in rt.get("Associations", []):
                if a.get("RouteTableAssociationId"):
                    attempt("disassociate route table",
                            lambda aid=a["RouteTableAssociationId"]: ec2.disassociate_route_table(AssociationId=aid))
            attempt(f"delete route table {rt['RouteTableId']}",
                    lambda r=rt: ec2.delete_route_table(RouteTableId=r["RouteTableId"]))

        for igw in ec2.describe_internet_gateways(
                Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"]:
            attempt("detach internet gateway",
                    lambda g=igw: ec2.detach_internet_gateway(InternetGatewayId=g["InternetGatewayId"], VpcId=vpc_id))
            attempt("delete internet gateway",
                    lambda g=igw: ec2.delete_internet_gateway(InternetGatewayId=g["InternetGatewayId"]))

        for sg in ec2.describe_security_groups(Filters=flt)["SecurityGroups"]:
            if sg["GroupName"] == "default":
                continue
            attempt(f"delete security group {sg['GroupName']}",
                    lambda s=sg: ec2.delete_security_group(GroupId=s["GroupId"]))

        for s in ec2.describe_subnets(Filters=flt)["Subnets"]:
            attempt(f"delete subnet {s['SubnetId']}", lambda sid=s["SubnetId"]: ec2.delete_subnet(SubnetId=sid))

        attempt(f"delete VPC {vpc_id}", lambda: ec2.delete_vpc(VpcId=vpc_id))
        logger.info(f"Deleted VPC {self.name} ({vpc_id})")

    # ---------------------------------------------------------------- verify

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.read_id, region=self.region)
        tag = f"vpc:{self.name}"
        if not live:
            return [VerifyResult(name=tag, passed=False, message="VPC does not exist")]
        ec2 = session.client("ec2", region_name=self.region)
        results = []

        sg = self._default_sg(ec2, live.vpc_id)
        if sg:
            rules = len(sg.get("IpPermissions", [])) + len(sg.get("IpPermissionsEgress", []))
            results.append(VerifyResult(
                name=f"{tag}:default-sg-empty", passed=rules == 0,
                message="default security group has no rules" if rules == 0
                        else f"default security group has {rules} rule(s) — anything placed in it by mistake is reachable"))

        route_tables = ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [live.vpc_id]}])["RouteTables"]
        main_rt = next((rt for rt in route_tables if any(a.get("Main") for a in rt.get("Associations", []))), None)
        rt_for = {a["SubnetId"]: rt for rt in route_tables for a in rt.get("Associations", []) if a.get("SubnetId")}
        leaking = [s for s in live.private_subnet_ids or [] if _has_igw_route(rt_for.get(s, main_rt) or {})]
        if live.private_subnet_ids:
            results.append(VerifyResult(
                name=f"{tag}:private-subnets-offline", passed=not leaking,
                message=f"{len(live.private_subnet_ids)} private subnet(s) have no route to an internet gateway"
                        if not leaking else f"private subnet(s) route to the internet gateway: {', '.join(leaking)}"))
            subnets = ec2.describe_subnets(SubnetIds=live.private_subnet_ids)["Subnets"]
            auto_ip = [s["SubnetId"] for s in subnets if s.get("MapPublicIpOnLaunch")]
            results.append(VerifyResult(
                name=f"{tag}:private-no-auto-public-ip", passed=not auto_ip,
                message="private subnets do not assign public IPs" if not auto_ip
                        else f"private subnet(s) auto-assign public IPs: {', '.join(auto_ip)}"))

        if not live.existing and "s3" in (self.endpoints or []):
            ep_id = (live.endpoint_ids or {}).get("s3")
            attached = set()
            if ep_id:
                ep = ec2.describe_vpc_endpoints(VpcEndpointIds=[ep_id])["VpcEndpoints"][0]
                attached = set(ep.get("RouteTableIds", []))
            ours = {rt["RouteTableId"] for rt in route_tables
                    if not any(a.get("Main") for a in rt.get("Associations", []))}
            missing = ours - attached
            results.append(VerifyResult(
                name=f"{tag}:s3-endpoint", passed=bool(ep_id) and not missing,
                message="S3 gateway endpoint on every route table — no NAT charges for S3 traffic" if ep_id and not missing
                        else ("no S3 gateway endpoint" if not ep_id else f"S3 endpoint missing from route table(s) {', '.join(sorted(missing))}")))
        return results
