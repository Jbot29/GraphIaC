from typing import ClassVar, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode

from ..logs import setup_logger

logger = setup_logger()

# CloudFront's fixed hosted zone ID used for all alias records pointing at CF distributions
CLOUDFRONT_HOSTED_ZONE_ID = "Z2FDTNDATAQYW2"


class HostedZone(BaseNode):
    deploy_actions: ClassVar[list] = [
        "route53:ListHostedZones",
        "route53:GetHostedZone",
    ]

    domain_name: str
    zone_id: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.domain_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        route53 = session.client("route53")
        try:
            resp = route53.list_hosted_zones()
            for zone in resp["HostedZones"]:
                if zone["Name"] == read_id.rstrip(".") + ".":
                    return HostedZone(g_id=g_id, domain_name=read_id, zone_id=zone["Id"])
        except ClientError as e:
            logger.error(f"Error reading hosted zone {read_id}: {e}")
        return None

    def create(self, session, G):
        raise NotImplementedError(
            "HostedZone should be imported, not created. "
            "Add it to the graph and let plan() detect it as IMPORT."
        )

    def update(self, session, G):
        pass

    def delete(self, session, G):
        route53 = session.client("route53")
        try:
            route53.delete_hosted_zone(Id=self.zone_id)
        except ClientError:
            raise


class Route53AliasRecord(BaseNode):
    deploy_actions: ClassVar[list] = [
        "route53:ChangeResourceRecordSets",
        "route53:ListResourceRecordSets",
    ]

    """An A alias record in Route53 — typically used to point a domain at a CloudFront distribution."""

    domain_name: str  # e.g. "begriff.co" or "www.begriff.co"
    hosted_zone_id: str  # Route53 hosted zone ID
    alias_dns_name: str  # target DNS name, e.g. "xxxx.cloudfront.net"
    alias_hosted_zone_id: str = CLOUDFRONT_HOSTED_ZONE_ID  # fixed for CloudFront targets

    @property
    def read_id(self) -> Optional[str]:
        return self.domain_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        # read_id is domain_name; we need hosted_zone_id from the node stored in the graph
        node = G.nodes.get(g_id, {}).get("data")
        if node is None or not getattr(node, "hosted_zone_id", None):
            return None

        route53 = session.client("route53")
        try:
            resp = route53.list_resource_record_sets(
                HostedZoneId=node.hosted_zone_id,
                StartRecordName=read_id,
                StartRecordType="A",
                MaxItems="1",
            )
            for rrs in resp.get("ResourceRecordSets", []):
                if rrs["Name"].rstrip(".") == read_id.rstrip(".") and rrs["Type"] == "A":
                    alias = rrs.get("AliasTarget", {})
                    return cls(
                        g_id=g_id,
                        domain_name=read_id,
                        hosted_zone_id=node.hosted_zone_id,
                        alias_dns_name=alias.get("DNSName", ""),
                        alias_hosted_zone_id=alias.get("HostedZoneId", CLOUDFRONT_HOSTED_ZONE_ID),
                    )
        except ClientError as e:
            logger.error(f"Error reading Route53 record {read_id}: {e}")
        return None

    def create(self, session, G):
        route53 = session.client("route53")
        try:
            route53.change_resource_record_sets(
                HostedZoneId=self.hosted_zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "UPSERT",
                            "ResourceRecordSet": {
                                "Name": self.domain_name,
                                "Type": "A",
                                "AliasTarget": {
                                    "HostedZoneId": self.alias_hosted_zone_id,
                                    "DNSName": self.alias_dns_name,
                                    "EvaluateTargetHealth": False,
                                },
                            },
                        }
                    ]
                },
            )
            logger.info(f"Created Route53 alias record {self.domain_name} -> {self.alias_dns_name}")
        except ClientError as e:
            logger.error(f"Failed to create Route53 alias record: {e}")
            raise

    def update(self, session, G, diff=None):
        self.create(session, G)  # UPSERT handles both create and update

    def delete(self, session, G):
        route53 = session.client("route53")
        try:
            route53.change_resource_record_sets(
                HostedZoneId=self.hosted_zone_id,
                ChangeBatch={
                    "Changes": [
                        {
                            "Action": "DELETE",
                            "ResourceRecordSet": {
                                "Name": self.domain_name,
                                "Type": "A",
                                "AliasTarget": {
                                    "HostedZoneId": self.alias_hosted_zone_id,
                                    "DNSName": self.alias_dns_name,
                                    "EvaluateTargetHealth": False,
                                },
                            },
                        }
                    ]
                },
            )
            logger.info(f"Deleted Route53 alias record {self.domain_name}")
        except ClientError as e:
            logger.error(f"Failed to delete Route53 alias record: {e}")
            raise
