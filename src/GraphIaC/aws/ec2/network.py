"""Default-VPC discovery and security-group plumbing.

Two decisions live in this file, and both are about not making a founder
build a network before they can deploy an app.

**No VPC of your own.** A fresh AWS account already has a default VPC with
a public subnet in every availability zone. Fargate tasks placed there with
`assignPublicIp` reach the internet — and ECR, and Secrets Manager —
without a NAT gateway, which would otherwise add ~$32/month to a stack that
hasn't served a request yet. Nodes take optional `vpc_id`/`subnet_ids`; when
unset they discover the default. Real network isolation is a later chapter
and a bigger bill.

**Security groups are not a node type.** A security group with no rules is
meaningless, and its rules are always *about a relationship*: "the load
balancer may reach the tasks", "the tasks may reach the database". So every
compute node here owns exactly one group, named after itself, created and
destroyed with it — and the edges open the ports between them. `app -> db`
is the rule.

Everything in here is idempotent: `ensure_*` finds-or-creates and returns
the id, so re-running a create is safe.
"""

from typing import List, Optional

from botocore.exceptions import ClientError

from ...logs import setup_logger

logger = setup_logger()

# The IAM actions this module's calls need. Nodes and edges that use these
# helpers fold this into their own deploy_actions.
NETWORK_ACTIONS = [
    "ec2:DescribeVpcs",
    "ec2:DescribeSubnets",
    "ec2:DescribeSecurityGroups",
    "ec2:CreateSecurityGroup",
    "ec2:DeleteSecurityGroup",
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupIngress",
    "ec2:CreateTags",
]


def default_vpc(session, region: str) -> Optional[str]:
    """The account's default VPC id, or None if it was deleted."""
    ec2 = session.client("ec2", region_name=region)
    resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        logger.error(
            f"No default VPC in {region}. Either recreate it "
            f"(`aws ec2 create-default-vpc --region {region}`) or set vpc_id "
            f"and subnet_ids explicitly."
        )
        return None
    return vpcs[0]["VpcId"]


def default_subnets(session, region: str, vpc_id: str) -> List[str]:
    """The VPC's subnets, one per availability zone, in stable AZ order.

    An ALB needs at least two AZs; ECS is happy with one but spreads across
    whatever it's given.
    """
    ec2 = session.client("ec2", region_name=region)
    resp = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])
    by_az = {}
    for subnet in resp.get("Subnets", []):
        by_az.setdefault(subnet["AvailabilityZone"], subnet["SubnetId"])
    return [by_az[az] for az in sorted(by_az)]


def resolve_network(session, region: str, vpc_id=None, subnet_ids=None):
    """(vpc_id, subnet_ids), filling either from the default VPC."""
    vpc_id = vpc_id or default_vpc(session, region)
    if not vpc_id:
        return None, []
    return vpc_id, list(subnet_ids) if subnet_ids else default_subnets(session, region, vpc_id)


def find_security_group(session, region: str, name: str, vpc_id: str) -> Optional[str]:
    ec2 = session.client("ec2", region_name=region)
    resp = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )
    groups = resp.get("SecurityGroups", [])
    return groups[0]["GroupId"] if groups else None


def ensure_security_group(session, region: str, name: str, description: str, vpc_id: str) -> str:
    """Find-or-create the group this resource owns. Returns its id."""
    existing = find_security_group(session, region, name, vpc_id)
    if existing:
        return existing

    ec2 = session.client("ec2", region_name=region)
    resp = ec2.create_security_group(GroupName=name, Description=description, VpcId=vpc_id)
    sg_id = resp["GroupId"]
    logger.info(f"Created security group {name} ({sg_id})")
    try:
        ec2.create_tags(Resources=[sg_id], Tags=[{"Key": "ManagedBy", "Value": "GraphIaC"}])
    except ClientError as e:  # tagging is a nicety, not a requirement
        logger.debug(f"Could not tag {sg_id}: {e}")
    return sg_id


def delete_security_group(session, region: str, sg_id: str) -> None:
    if not sg_id:
        return
    ec2 = session.client("ec2", region_name=region)
    try:
        ec2.delete_security_group(GroupId=sg_id)
        logger.info(f"Deleted security group {sg_id}")
    except ClientError as e:
        # DependencyViolation means something is still attached — the ENI
        # teardown after a service delete is asynchronous and can lag.
        logger.warning(f"Could not delete security group {sg_id}: {e}")


def _permission(port: int, source_sg_id=None, cidr=None, description=""):
    ip_range = {"IpProtocol": "tcp", "FromPort": port, "ToPort": port}
    if source_sg_id:
        ip_range["UserIdGroupPairs"] = [{"GroupId": source_sg_id, "Description": description}]
    else:
        ip_range["IpRanges"] = [{"CidrIp": cidr, "Description": description}]
    return ip_range


def has_ingress(session, region: str, sg_id: str, port: int, source_sg_id=None, cidr=None) -> bool:
    """Is this exact rule already on the group? The independent read the
    guards and `read()` implementations use — no create-and-catch."""
    ec2 = session.client("ec2", region_name=region)
    try:
        groups = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"]
    except ClientError:
        return False
    for perm in groups[0].get("IpPermissions", []):
        if perm.get("IpProtocol") != "tcp":
            continue
        if perm.get("FromPort") != port or perm.get("ToPort") != port:
            continue
        if source_sg_id:
            if any(p.get("GroupId") == source_sg_id for p in perm.get("UserIdGroupPairs", [])):
                return True
        elif cidr:
            if any(r.get("CidrIp") == cidr for r in perm.get("IpRanges", [])):
                return True
    return False


def ensure_ingress(session, region: str, sg_id: str, port: int,
                   source_sg_id=None, cidr=None, description="") -> bool:
    """Open one port on `sg_id`, from another group or a CIDR. Idempotent."""
    if has_ingress(session, region, sg_id, port, source_sg_id, cidr):
        return False

    ec2 = session.client("ec2", region_name=region)
    try:
        ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[_permission(port, source_sg_id, cidr, description)],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidPermission.Duplicate":
            return False
        raise
    logger.info(f"Opened tcp/{port} on {sg_id} from {source_sg_id or cidr}")
    return True


def revoke_ingress(session, region: str, sg_id: str, port: int,
                   source_sg_id=None, cidr=None) -> None:
    ec2 = session.client("ec2", region_name=region)
    try:
        ec2.revoke_security_group_ingress(
            GroupId=sg_id, IpPermissions=[_permission(port, source_sg_id, cidr)]
        )
        logger.info(f"Closed tcp/{port} on {sg_id} from {source_sg_id or cidr}")
    except ClientError as e:
        if e.response["Error"]["Code"] not in ("InvalidPermission.NotFound", "InvalidGroup.NotFound"):
            raise
