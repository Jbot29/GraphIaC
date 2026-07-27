"""Application Load Balancer — the public front door for a container app.

The node owns three things AWS treats separately: the load balancer, its
security group (80 and 443 from the internet, nothing else), and an HTTP
listener. The listener's default action is a 503 until something is wired
behind it — an ALB with no target group is a 503 either way, and this makes
it a deliberate one.

Two edges finish the job:

    cert -> alb    ACMCertificateALBEdge — the HTTPS listener, and a 301
                   from :80. Gates the ALB until the certificate is ISSUED.
    alb  -> hz     ALBRoute53Edge — an A alias record at your domain.

The target group and the "let the load balancer reach the tasks" rule
belong to `alb -> app` (see aws/ecs.py) — they are about the relationship,
not about the load balancer.
"""

from typing import ClassVar, List, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode, VerifyResult

from ...logs import setup_logger
from ..types import AwsName
from .network import (
    NETWORK_ACTIONS,
    delete_security_group,
    ensure_ingress,
    ensure_security_group,
    resolve_network,
)

logger = setup_logger()

ELB_ACTIONS = [
    "elasticloadbalancing:CreateLoadBalancer",
    "elasticloadbalancing:DescribeLoadBalancers",
    "elasticloadbalancing:DeleteLoadBalancer",
    "elasticloadbalancing:SetSubnets",
    "elasticloadbalancing:CreateListener",
    "elasticloadbalancing:DescribeListeners",
    "elasticloadbalancing:ModifyListener",
    "elasticloadbalancing:DeleteListener",
    "elasticloadbalancing:AddTags",
    "elasticloadbalancing:DescribeTags",
]

# Sent when a request arrives before anything is wired behind the listener.
NOTHING_BEHIND_ME = {
    "Type": "fixed-response",
    "FixedResponseConfig": {
        "StatusCode": "503",
        "ContentType": "text/plain",
        "MessageBody": "no service attached to this load balancer yet\n",
    },
}

REDIRECT_TO_HTTPS = {
    "Type": "redirect",
    "RedirectConfig": {
        "Protocol": "HTTPS",
        "Port": "443",
        "StatusCode": "HTTP_301",
    },
}


def _listener_on(session, region, lb_arn, port):
    """The listener on a given port, or None. Raw describe — no caching,
    because listeners are edited by two different edges."""
    elb = session.client("elbv2", region_name=region)
    try:
        listeners = elb.describe_listeners(LoadBalancerArn=lb_arn)["Listeners"]
    except ClientError:
        return None
    for listener in listeners:
        if listener.get("Port") == port:
            return listener
    return None


class ALB(BaseNode):
    deploy_actions: ClassVar[list] = ELB_ACTIONS + NETWORK_ACTIONS

    name: AwsName
    region: str = "us-east-2"
    scheme: str = "internet-facing"

    # Unset means "the default VPC and all of its subnets" — see network.py.
    vpc_id: Optional[str] = None
    subnet_ids: Optional[List[str]] = None

    # populated by AWS
    arn: Optional[str] = None
    dns_name: Optional[str] = None
    canonical_hosted_zone_id: Optional[str] = None
    security_group_id: Optional[str] = None
    state: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    def ready(self) -> bool:
        """An ALB is 'provisioning' for a minute or two after creation and
        cannot serve traffic until it is active."""
        return self.state == "active"

    @property
    def sg_name(self) -> str:
        return f"{self.name}-alb-sg"

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        node = G.nodes.get(g_id, {}).get("data")
        region = kwargs.get("region") or getattr(node, "region", None) or "us-east-2"
        elb = session.client("elbv2", region_name=region)
        try:
            lbs = elb.describe_load_balancers(Names=[read_id])["LoadBalancers"]
        except ClientError as e:
            if e.response["Error"]["Code"] in ("LoadBalancerNotFound", "ValidationError"):
                return None
            raise
        if not lbs:
            return None
        lb = lbs[0]
        sgs = lb.get("SecurityGroups") or []
        return cls(
            g_id=g_id,
            name=read_id,
            region=region,
            scheme=lb.get("Scheme", "internet-facing"),
            vpc_id=lb.get("VpcId"),
            subnet_ids=[az["SubnetId"] for az in lb.get("AvailabilityZones", [])],
            arn=lb["LoadBalancerArn"],
            dns_name=lb.get("DNSName"),
            canonical_hosted_zone_id=lb.get("CanonicalHostedZoneId"),
            security_group_id=sgs[0] if sgs else None,
            state=(lb.get("State") or {}).get("Code"),
        )

    def create(self, session, G):
        vpc_id, subnet_ids = resolve_network(session, self.region, self.vpc_id, self.subnet_ids)
        if not vpc_id or len(subnet_ids) < 2:
            logger.error(
                f"ALB {self.name} needs subnets in at least two availability zones "
                f"(found {len(subnet_ids)} in {vpc_id or 'no vpc'})"
            )
            return False
        self.vpc_id, self.subnet_ids = vpc_id, subnet_ids

        # The load balancer's own group: the public half of the stack, and
        # the only thing here that faces the internet.
        self.security_group_id = ensure_security_group(
            session, self.region, self.sg_name,
            f"GraphIaC: public ingress for {self.name}", vpc_id,
        )
        for port in (80, 443):
            ensure_ingress(session, self.region, self.security_group_id, port,
                           cidr="0.0.0.0/0", description="public web traffic")

        elb = session.client("elbv2", region_name=self.region)
        resp = elb.create_load_balancer(
            Name=self.name,
            Subnets=subnet_ids,
            SecurityGroups=[self.security_group_id],
            Scheme=self.scheme,
            Type="application",
            IpAddressType="ipv4",
            Tags=[{"Key": "ManagedBy", "Value": "GraphIaC"}],
        )
        lb = resp["LoadBalancers"][0]
        self.arn = lb["LoadBalancerArn"]
        self.dns_name = lb.get("DNSName")
        self.canonical_hosted_zone_id = lb.get("CanonicalHostedZoneId")
        self.state = (lb.get("State") or {}).get("Code")

        elb.create_listener(
            LoadBalancerArn=self.arn,
            Protocol="HTTP",
            Port=80,
            DefaultActions=[NOTHING_BEHIND_ME],
        )
        logger.info(f"Created ALB {self.name} at {self.dns_name} (state: {self.state})")
        return True

    def update(self, session, G, diff=None):
        """Subnets are the only field worth reconciling in place — name and
        scheme changes are a replacement in AWS, not an update."""
        if not (self.arn and self.subnet_ids):
            return
        elb = session.client("elbv2", region_name=self.region)
        elb.set_subnets(LoadBalancerArn=self.arn, Subnets=self.subnet_ids)

    def delete(self, session, G):
        elb = session.client("elbv2", region_name=self.region)
        if self.arn:
            try:
                elb.delete_load_balancer(LoadBalancerArn=self.arn)
                logger.info(f"Deleted ALB {self.name}")
            except ClientError as e:
                logger.error(f"Could not delete ALB {self.name}: {e}")
        # The ENIs behind the load balancer take a moment to disappear, and
        # the group can't go until they have — delete_security_group warns
        # rather than raising when that race bites.
        if self.security_group_id:
            delete_security_group(session, self.region, self.security_group_id)

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.name, region=self.region)
        if not live:
            return [VerifyResult(name=f"alb:{self.name}", passed=False,
                                 message="load balancer does not exist")]

        results = [VerifyResult(
            name=f"alb:{self.name}:active", passed=live.state == "active",
            message=f"state is {live.state}",
        )]

        https = _listener_on(session, self.region, live.arn, 443)
        results.append(VerifyResult(
            name=f"alb:{self.name}:https", passed=bool(https),
            message="HTTPS listener on 443" if https else "no HTTPS listener — traffic is plaintext",
        ))

        http = _listener_on(session, self.region, live.arn, 80)
        redirects = bool(http) and any(
            a.get("Type") == "redirect" for a in http.get("DefaultActions", [])
        )
        results.append(VerifyResult(
            name=f"alb:{self.name}:http-redirect",
            # Nothing to redirect to until there is an HTTPS listener.
            passed=redirects or not https,
            message="port 80 redirects to HTTPS" if redirects else "port 80 serves plaintext",
        ))
        return results


class ACMCertificateALBEdge(BaseEdge):
    """The HTTPS listener, and the 301 that makes port 80 pointless.

    Gating: an ALB cannot serve HTTPS with a certificate that is still
    PENDING_VALIDATION, so the whole load balancer waits for ISSUED rather
    than coming up as a plaintext endpoint in the meantime.
    """

    deploy_actions: ClassVar[list] = [
        "elasticloadbalancing:CreateListener",
        "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:ModifyListener",
        "elasticloadbalancing:DeleteListener",
        "acm:DescribeCertificate",
    ]

    gates_destination: ClassVar[bool] = True

    cert_g_id: str
    alb_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.cert_g_id

    @property
    def destination_g_id(self) -> str:
        return self.alb_g_id

    def read(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        cert = G.nodes[self.cert_g_id]["data"]
        if not alb.arn or not cert.arn:
            return None
        listener = _listener_on(session, alb.region, alb.arn, 443)
        if not listener:
            return None
        attached = {c["CertificateArn"] for c in listener.get("Certificates", [])}
        return self if cert.arn in attached else None

    def create(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        cert = G.nodes[self.cert_g_id]["data"]
        if not alb.arn or not cert.arn:
            logger.warning("ALB or certificate ARN not available yet; skipping HTTPS listener")
            return False

        elb = session.client("elbv2", region_name=alb.region)

        # Whatever port 80 currently forwards to is what HTTPS should
        # forward to — otherwise attaching a certificate would take the site
        # down until `alb -> app` ran again.
        http = _listener_on(session, alb.region, alb.arn, 80)
        default = NOTHING_BEHIND_ME
        if http:
            forwards = [a for a in http.get("DefaultActions", []) if a.get("Type") == "forward"]
            if forwards:
                default = {"Type": "forward", "TargetGroupArn": forwards[0]["TargetGroupArn"]}

        https = _listener_on(session, alb.region, alb.arn, 443)
        if https:
            elb.modify_listener(ListenerArn=https["ListenerArn"],
                                Certificates=[{"CertificateArn": cert.arn}])
        else:
            elb.create_listener(
                LoadBalancerArn=alb.arn,
                Protocol="HTTPS",
                Port=443,
                SslPolicy="ELBSecurityPolicy-TLS13-1-2-2021-06",
                Certificates=[{"CertificateArn": cert.arn}],
                DefaultActions=[default],
            )
            logger.info(f"Created HTTPS listener on {alb.name}")

        if http:
            elb.modify_listener(ListenerArn=http["ListenerArn"],
                                DefaultActions=[REDIRECT_TO_HTTPS])
            logger.info(f"Port 80 on {alb.name} now redirects to HTTPS")
        return True

    def update(self, session, G, diff=None):
        return self.create(session, G)

    def delete(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        if not alb.arn:
            return
        elb = session.client("elbv2", region_name=alb.region)
        https = _listener_on(session, alb.region, alb.arn, 443)
        if https:
            elb.delete_listener(ListenerArn=https["ListenerArn"])
        http = _listener_on(session, alb.region, alb.arn, 80)
        if http:
            elb.modify_listener(ListenerArn=http["ListenerArn"],
                                DefaultActions=[NOTHING_BEHIND_ME])

    def verify(self, session, G) -> list:
        alb = G.nodes[self.alb_g_id]["data"]
        if not alb.arn:
            return []
        listener = _listener_on(session, alb.region, alb.arn, 443)
        if not listener:
            return [VerifyResult(name=f"alb:{alb.name}:tls", passed=False,
                                 message="no HTTPS listener")]
        policy = listener.get("SslPolicy", "")
        return [VerifyResult(
            name=f"alb:{alb.name}:tls", passed="TLS13" in policy or "TLS-1-2" in policy,
            message=f"ssl policy {policy}",
        )]


class ALBRoute53Edge(BaseEdge):
    """An A alias record pointing a domain at the load balancer.

    Reads the ALB's DNS name and canonical hosted zone from the graph at
    create() time, so it works in the same run that created the ALB.
    """

    deploy_actions: ClassVar[list] = [
        "route53:ChangeResourceRecordSets",
        "route53:ListResourceRecordSets",
        "elasticloadbalancing:DescribeLoadBalancers",
    ]

    alb_g_id: str
    hz_g_id: str
    domain_name: str

    @property
    def source_g_id(self) -> str:
        return self.alb_g_id

    @property
    def destination_g_id(self) -> str:
        return self.hz_g_id

    def _alias(self, alb):
        return {
            "HostedZoneId": alb.canonical_hosted_zone_id,
            "DNSName": alb.dns_name,
            "EvaluateTargetHealth": True,
        }

    def read(self, session, G):
        alb = G.nodes[self.alb_g_id]["data"]
        hz = G.nodes[self.hz_g_id]["data"]
        if not alb.dns_name or not hz.zone_id:
            return None
        route53 = session.client("route53")
        try:
            resp = route53.list_resource_record_sets(
                HostedZoneId=hz.zone_id, StartRecordName=self.domain_name,
                StartRecordType="A", MaxItems="1",
            )
        except ClientError as e:
            logger.error(f"Error reading alias record for {self.domain_name}: {e}")
            return None
        for rrs in resp.get("ResourceRecordSets", []):
            if rrs["Name"].rstrip(".") == self.domain_name.rstrip(".") and rrs["Type"] == "A":
                target = (rrs.get("AliasTarget") or {}).get("DNSName", "").rstrip(".")
                if target.lower() == (alb.dns_name or "").rstrip(".").lower():
                    return self
        return None

    def _change(self, session, G, action):
        alb = G.nodes[self.alb_g_id]["data"]
        hz = G.nodes[self.hz_g_id]["data"]
        if not (alb.dns_name and alb.canonical_hosted_zone_id and hz.zone_id):
            logger.warning(f"ALB DNS name not available yet; skipping alias for {self.domain_name}")
            return False
        session.client("route53").change_resource_record_sets(
            HostedZoneId=hz.zone_id,
            ChangeBatch={"Changes": [{
                "Action": action,
                "ResourceRecordSet": {
                    "Name": self.domain_name,
                    "Type": "A",
                    "AliasTarget": self._alias(alb),
                },
            }]},
        )
        logger.info(f"{action} alias {self.domain_name} -> {alb.dns_name}")
        return True

    def create(self, session, G):
        return self._change(session, G, "UPSERT")

    def update(self, session, G, diff=None):
        return self._change(session, G, "UPSERT")

    def delete(self, session, G):
        try:
            self._change(session, G, "DELETE")
        except ClientError as e:
            logger.warning(f"Could not delete alias {self.domain_name}: {e}")
