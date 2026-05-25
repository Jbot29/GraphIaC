import json
from typing import Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode

from ..logs import setup_logger

logger = setup_logger()


class S3Bucket(BaseNode):
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
