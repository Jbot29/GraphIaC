from typing import Optional, List
from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode, BaseEdge
from .iam_role import IAMRoleInlinePolicyEdge
from .iam_policy import IamPolicyDocument, IamPolicyStatement, get_inline_policy_for_role, put_inline_policy_for_role
from ..logs import setup_logger

logger = setup_logger()


class SESDomainIdentity(BaseNode):
    domain: str
    region: str = "us-east-1"
    dkim_tokens: Optional[List[str]] = None
    verification_status: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.domain

    def read_arn(self, session) -> str:
        sts = session.client("sts")
        account_id = sts.get_caller_identity()["Account"]
        return f"arn:aws:ses:{self.region}:{account_id}:identity/{self.domain}"

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        region = kwargs.get("region", "us-east-1")
        ses = session.client("sesv2", region_name=region)
        try:
            resp = ses.get_email_identity(EmailIdentity=read_id)
        except ses.exceptions.NotFoundException:
            return None
        except ClientError as e:
            logger.error(f"Error reading SES identity {read_id}: {e}")
            return None

        dkim_tokens = resp.get("DkimAttributes", {}).get("Tokens") or []
        return SESDomainIdentity(
            g_id=g_id,
            domain=read_id,
            region=region,
            dkim_tokens=dkim_tokens,
            verification_status=resp.get("VerificationStatus"),
        )

    def create(self, session, G):
        ses = session.client("sesv2", region_name=self.region)
        resp = ses.create_email_identity(
            EmailIdentity=self.domain,
            DkimSigningAttributes={"NextSigningKeyLength": "RSA_2048_BIT"},
        )
        self.dkim_tokens = resp.get("DkimAttributes", {}).get("Tokens") or []
        logger.info(f"Created SES identity {self.domain}, DKIM tokens: {self.dkim_tokens}")
        return True

    def update(self, session, G):
        pass

    def delete(self, session, G):
        ses = session.client("sesv2", region_name=self.region)
        try:
            ses.delete_email_identity(EmailIdentity=self.domain)
        except ClientError as e:
            raise


class SESDomainRoute53Edge(BaseEdge):
    """
    Wires an SES domain identity to a Route53 hosted zone by creating the
    three DKIM CNAME records that SES requires for domain verification.
    """
    ses_g_id: str
    zone_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.ses_g_id

    @property
    def destination_g_id(self) -> str:
        return self.zone_g_id

    def read(self, session, G):
        ses_node = G.nodes[self.ses_g_id]["data"]
        zone_node = G.nodes[self.zone_g_id]["data"]

        if not ses_node.dkim_tokens or not zone_node.zone_id:
            return None

        route53 = session.client("route53")
        try:
            expected = {f"{t}._domainkey.{ses_node.domain}" for t in ses_node.dkim_tokens}
            resp = route53.list_resource_record_sets(
                HostedZoneId=zone_node.zone_id,
                StartRecordName=f"{ses_node.dkim_tokens[0]}._domainkey.{ses_node.domain}",
                StartRecordType="CNAME",
                MaxItems="10",
            )
            found = {r["Name"].rstrip(".") for r in resp["ResourceRecordSets"] if r["Type"] == "CNAME"}
            if expected.issubset(found):
                return self
        except ClientError as e:
            logger.error(f"Error reading DKIM records: {e}")
        return None

    def create(self, session, G):
        ses_node = G.nodes[self.ses_g_id]["data"]
        zone_node = G.nodes[self.zone_g_id]["data"]

        if not ses_node.dkim_tokens:
            raise ValueError(f"SESDomainIdentity {self.ses_g_id} has no DKIM tokens — was it created?")

        route53 = session.client("route53")
        changes = [
            {
                "Action": "UPSERT",
                "ResourceRecordSet": {
                    "Name": f"{token}._domainkey.{ses_node.domain}",
                    "Type": "CNAME",
                    "TTL": 1800,
                    "ResourceRecords": [{"Value": f"{token}.dkim.amazonses.com"}],
                },
            }
            for token in ses_node.dkim_tokens
        ]

        route53.change_resource_record_sets(
            HostedZoneId=zone_node.zone_id,
            ChangeBatch={"Changes": changes},
        )
        logger.info(f"Created {len(changes)} DKIM CNAME records for {ses_node.domain}")
        return True

    def update(self, session, G):
        pass

    def delete(self, session, G):
        ses_node = G.nodes[self.ses_g_id]["data"]
        zone_node = G.nodes[self.zone_g_id]["data"]

        if not ses_node.dkim_tokens or not zone_node.zone_id:
            return

        route53 = session.client("route53")
        changes = [
            {
                "Action": "DELETE",
                "ResourceRecordSet": {
                    "Name": f"{token}._domainkey.{ses_node.domain}",
                    "Type": "CNAME",
                    "TTL": 1800,
                    "ResourceRecords": [{"Value": f"{token}.dkim.amazonses.com"}],
                },
            }
            for token in ses_node.dkim_tokens
        ]
        try:
            route53.change_resource_record_sets(
                HostedZoneId=zone_node.zone_id,
                ChangeBatch={"Changes": changes},
            )
        except ClientError as e:
            raise


class LambdaSESEdge(IAMRoleInlinePolicyEdge):
    """
    Grants a Lambda's execution role permission to send email via SES
    from the given domain identity.
    """
    role_g_id: str
    lambda_node_g_id: str
    ses_node_g_id: str
    policy_doc: Optional[IamPolicyDocument] = None

    @property
    def source_g_id(self) -> str:
        return self.lambda_node_g_id

    @property
    def destination_g_id(self) -> str:
        return self.ses_node_g_id

    def read(self, session, G):
        role_name = G.nodes[self.role_g_id]["data"].read_id
        p = get_inline_policy_for_role(session, role_name, self.policy_name)
        if not p:
            return None
        return LambdaSESEdge(
            role_g_id=self.role_g_id,
            lambda_node_g_id=self.lambda_node_g_id,
            ses_node_g_id=self.ses_node_g_id,
            policy_doc=p,
        )

    def create(self, session, G):
        role_name = G.nodes[self.role_g_id]["data"].read_id
        identity_arn = G.nodes[self.ses_node_g_id]["data"].read_arn(session)

        statement = IamPolicyStatement(
            Sid="SESSendEmail",
            Effect="Allow",
            Action=["ses:SendEmail", "ses:SendRawEmail"],
            Resource=[identity_arn],
        )
        self.policy_doc = IamPolicyDocument(Statement=[statement])
        put_inline_policy_for_role(session, role_name, self.policy_name, self.policy_doc)
        return True

    def update(self, session, G):
        pass

    def delete(self, session, G):
        pass
