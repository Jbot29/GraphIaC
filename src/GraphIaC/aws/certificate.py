from typing import Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode, VerifyResult

from ..logs import setup_logger

logger = setup_logger()


class ACMCertificate(BaseNode):
    domain_name: str
    arn: Optional[str] = None
    status: Optional[str] = None  # PENDING_VALIDATION, ISSUED, FAILED, EXPIRED, etc.

    @property
    def read_id(self) -> Optional[str]:
        # Prefer ARN for direct lookup; fall back to domain name search
        return self.arn if self.arn else self.domain_name

    def ready(self) -> bool:
        # A requested-but-unvalidated cert exists but can't be attached to
        # anything yet — dependents ($refs to cert.arn) stay BLOCKED until ISSUED.
        return self.status == "ISSUED"

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        # When called from infra.py with read_id=None, fall back to domain_name from graph
        if not read_id and G is not None:
            node = G.nodes.get(g_id, {}).get("data")
            if node and hasattr(node, "domain_name"):
                read_id = node.domain_name

        acm = session.client("acm", region_name="us-east-1")
        try:
            if read_id and read_id.startswith("arn:"):
                resp = acm.describe_certificate(CertificateArn=read_id)
                cert = resp["Certificate"]
                return cls(
                    g_id=g_id,
                    domain_name=cert["DomainName"],
                    arn=cert["CertificateArn"],
                    status=cert["Status"],
                )

            # Search by domain name
            paginator = acm.get_paginator("list_certificates")
            for page in paginator.paginate():
                for summary in page["CertificateSummaryList"]:
                    if summary["DomainName"] == read_id:
                        resp = acm.describe_certificate(CertificateArn=summary["CertificateArn"])
                        cert = resp["Certificate"]
                        return cls(
                            g_id=g_id,
                            domain_name=cert["DomainName"],
                            arn=cert["CertificateArn"],
                            status=cert["Status"],
                        )
        except ClientError as e:
            logger.error(f"Error reading ACM certificate {read_id}: {e}")
        return None

    def create(self, session, G):
        acm = session.client("acm", region_name="us-east-1")
        try:
            resp = acm.request_certificate(
                DomainName=self.domain_name,
                ValidationMethod="DNS",
                SubjectAlternativeNames=[f"www.{self.domain_name}"],
                Tags=[{"Key": "Name", "Value": f"{self.domain_name} Certificate"}],
            )
            self.arn = resp["CertificateArn"]
            self.status = "PENDING_VALIDATION"
            logger.info(f"ACM certificate requested for {self.domain_name}: {self.arn}")
        except ClientError as e:
            logger.error(f"Failed to request certificate for {self.domain_name}: {e}")
            raise

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.read_id)
        if not live:
            return [VerifyResult(name="Certificate exists", passed=False,
                                 message="certificate not found in ACM")]
        results = [
            VerifyResult(
                name="Certificate status",
                passed=live.status == "ISSUED",
                message=live.status,
            ),
            VerifyResult(
                name="Certificate domain",
                passed=live.domain_name == self.domain_name,
                message=live.domain_name,
            ),
        ]
        return results

    def update(self, session, G, diff=None):
        pass  # ACM cert properties are immutable; status is read-only from AWS

    def delete(self, session, G):
        if not self.arn:
            return
        acm = session.client("acm", region_name="us-east-1")
        try:
            acm.delete_certificate(CertificateArn=self.arn)
            logger.info(f"Deleted ACM certificate {self.arn}")
        except ClientError as e:
            logger.error(f"Failed to delete certificate {self.arn}: {e}")
            raise


class ACMCertificateHostedZoneEdge(BaseEdge):
    """
    Wires an ACMCertificate to a HostedZone by creating the DNS CNAME validation
    records in Route53. ACM requires these records to prove domain ownership before
    issuing the certificate.
    """

    cert_g_id: str
    hz_g_id: str

    @property
    def source_g_id(self):
        return self.cert_g_id

    @property
    def destination_g_id(self):
        return self.hz_g_id

    def read(self, session, G=None):
        if G is None:
            return None
        cert = G.nodes[self.cert_g_id]["data"]
        hz = G.nodes[self.hz_g_id]["data"]
        if not cert.arn or not hz.zone_id:
            return None

        # Check whether the validation CNAME records already exist in Route53
        route53 = session.client("route53")
        acm = session.client("acm", region_name="us-east-1")
        try:
            resp = acm.describe_certificate(CertificateArn=cert.arn)
            for option in resp["Certificate"].get("DomainValidationOptions", []):
                if "ResourceRecord" not in option:
                    return None
                record = option["ResourceRecord"]
                existing = route53.list_resource_record_sets(
                    HostedZoneId=hz.zone_id,
                    StartRecordName=record["Name"],
                    StartRecordType=record["Type"],
                    MaxItems="1",
                )
                sets = existing.get("ResourceRecordSets", [])
                if not sets or sets[0]["Name"].rstrip(".") != record["Name"].rstrip("."):
                    return None
            return self  # all validation records are present
        except ClientError as e:
            logger.error(f"Error checking DNS validation records: {e}")
        return None

    def create(self, session, G):
        cert = G.nodes[self.cert_g_id]["data"]
        hz = G.nodes[self.hz_g_id]["data"]

        if not cert.arn:
            logger.warning("Certificate ARN not yet available; skipping DNS validation setup")
            return

        acm = session.client("acm", region_name="us-east-1")
        route53 = session.client("route53")
        try:
            resp = acm.describe_certificate(CertificateArn=cert.arn)
            for option in resp["Certificate"].get("DomainValidationOptions", []):
                if option.get("ValidationStatus") == "SUCCESS":
                    continue
                if "ResourceRecord" not in option:
                    logger.warning(
                        f"DNS validation record not yet available for {option['DomainName']}; "
                        "re-run after ACM propagates the record"
                    )
                    continue
                dns_record = option["ResourceRecord"]
                route53.change_resource_record_sets(
                    HostedZoneId=hz.zone_id,
                    ChangeBatch={
                        "Changes": [
                            {
                                "Action": "UPSERT",
                                "ResourceRecordSet": {
                                    "Name": dns_record["Name"],
                                    "Type": dns_record["Type"],
                                    "TTL": 300,
                                    "ResourceRecords": [{"Value": dns_record["Value"]}],
                                },
                            }
                        ]
                    },
                )
                logger.info(f"Added DNS validation CNAME for {option['DomainName']}")
        except ClientError as e:
            logger.error(f"Failed to add DNS validation records: {e}")
            raise

    def update(self, session, G, diff=None):
        pass

    def delete(self, session, G):
        # Validation CNAMEs are harmless to leave in place and ACM reuses them on renewal.
        pass
