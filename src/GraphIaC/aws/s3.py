import json
from typing import ClassVar, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode, VerifyResult

from ..logs import setup_logger

logger = setup_logger()


class S3Bucket(BaseNode):
    deploy_actions: ClassVar[list] = [
        "s3:CreateBucket",
        "s3:DeleteBucket",
        "s3:ListBucket",
        "s3:ListBucketVersions",
        "s3:GetBucketLocation",
        "s3:PutBucketPublicAccessBlock",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketPolicy",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
    ]

    bucket_name: str
    region: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.bucket_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        s3 = session.client("s3")
        try:
            s3.head_bucket(Bucket=read_id)
            loc = s3.get_bucket_location(Bucket=read_id)
            region = loc["LocationConstraint"] or "us-east-1"
            return cls(g_id=g_id, bucket_name=read_id, region=region)
        except ClientError as e:
            if e.response["Error"]["Code"] in ("404", "NoSuchBucket"):
                return None
            raise

    def create(self, session, G):
        s3 = session.client("s3")
        try:
            if self.region is None or self.region == "us-east-1":
                s3.create_bucket(Bucket=self.bucket_name)
            else:
                s3.create_bucket(
                    Bucket=self.bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
            s3.put_public_access_block(
                Bucket=self.bucket_name,
                PublicAccessBlockConfiguration={
                    "BlockPublicAcls": True,
                    "IgnorePublicAcls": True,
                    "BlockPublicPolicy": True,
                    "RestrictPublicBuckets": True,
                },
            )
            logger.info(f"Created private S3 bucket: {self.bucket_name}")
        except ClientError as e:
            logger.error(f"Failed to create S3 bucket {self.bucket_name}: {e}")
            raise

    def update(self, session, G, diff=None):
        pass

    def verify(self, session, G) -> list:
        s3 = session.client("s3")
        results = []

        # Check public access block
        try:
            resp = s3.get_public_access_block(Bucket=self.bucket_name)
            cfg = resp["PublicAccessBlockConfiguration"]
            all_blocked = all([
                cfg.get("BlockPublicAcls"),
                cfg.get("IgnorePublicAcls"),
                cfg.get("BlockPublicPolicy"),
                cfg.get("RestrictPublicBuckets"),
            ])
            results.append(VerifyResult(
                name="Public access block",
                passed=all_blocked,
                message="all four settings enabled" if all_blocked else str(cfg),
            ))
        except ClientError:
            results.append(VerifyResult(
                name="Public access block",
                passed=False,
                message="no public access block configured",
            ))

        # Check bucket policy locks to CloudFront only
        try:
            resp = s3.get_bucket_policy(Bucket=self.bucket_name)
            policy = json.loads(resp["Policy"])
            cf_locked = any(
                stmt.get("Principal", {}).get("Service") == "cloudfront.amazonaws.com"
                and "AWS:SourceArn" in stmt.get("Condition", {}).get("StringEquals", {})
                for stmt in policy.get("Statement", [])
            )
            public_grant = any(
                stmt.get("Principal") in ("*", {"AWS": "*"})
                and stmt.get("Effect") == "Allow"
                for stmt in policy.get("Statement", [])
            )
            results.append(VerifyResult(
                name="Bucket policy: CloudFront OAC scoped",
                passed=cf_locked,
                message="policy restricts access to CloudFront distribution" if cf_locked
                        else "no CloudFront OAC condition found in policy",
            ))
            results.append(VerifyResult(
                name="Bucket policy: no public Allow",
                passed=not public_grant,
                message="no public grants" if not public_grant
                        else "WARNING: policy grants public access",
            ))
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
                results.append(VerifyResult(
                    name="Bucket policy",
                    passed=False,
                    message="no bucket policy — bucket may be accessible without restriction",
                ))

        return results

    def set_bucket_policy(self, session, policy: dict):
        s3 = session.client("s3")
        s3.put_bucket_policy(Bucket=self.bucket_name, Policy=json.dumps(policy))

    def delete(self, session, G):
        s3 = session.resource("s3")
        bucket = s3.Bucket(self.bucket_name)
        try:
            bucket.objects.all().delete()
            bucket.object_versions.all().delete()
            bucket.delete()
            logger.info(f"Deleted S3 bucket {self.bucket_name}")
        except Exception as e:
            logger.error(f"Failed to delete S3 bucket {self.bucket_name}: {e}")
            raise
