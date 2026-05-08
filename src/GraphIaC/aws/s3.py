from typing import Optional

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel


class S3Bucket(BaseModel):
    g_id: str
    bucket_name: str
    region: Optional[str] = None

    def exists(self, session):
        print(f"{self.__class__.__name__}: Exists {self}")

        if s3_bucket_exists(session, self.bucket_name):
            return True

        return False

    def create(self, session, G):
        return create_s3_bucket(session, self.bucket_name, self.region)

    def delete(self, session, G):
        delete_s3_bucket(session, self.bucket_name, self.region)


def s3_bucket_exists(session, bucket_name):
    # Initialize the S3 client
    s3 = session.client("s3")

    try:
        # Try to get the bucket's information (this will fail if the bucket doesn't exist)
        s3.head_bucket(Bucket=bucket_name)
        print(f"Bucket {bucket_name} exists.")
        return True
    except ClientError as e:
        # Check if the error code is 404 (Not Found)
        error_code = int(e.response["Error"]["Code"])
        if error_code == 404:
            print(f"Bucket {bucket_name} does not exist.")
        else:
            print(f"Error occurred: {e}")
        return False


def create_s3_bucket(session, bucket_name, region):
    # Create an S3 client
    s3_client = session.client("s3")

    try:
        # If a region is specified, create the bucket with the region
        if region is None and region != "us-east-1":
            response = s3_client.create_bucket(Bucket=bucket_name)
        else:
            response = s3_client.create_bucket(
                Bucket=bucket_name, CreateBucketConfiguration={"LocationConstraint": region}
            )
        print(f"Bucket {bucket_name} created successfully.")
        return response
    except ClientError as e:
        print(f"Error: {e}")
        return None


def set_private_s3_bucket(bucket_name, region=None):
    # Initialize the S3 client
    s3_client = boto3.client("s3", region_name=region)

    try:
        # Block public access for the bucket
        s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )
        print(f"Public access blocked for bucket {bucket_name}.")

        # Ensure the ACL is private
        s3_client.put_bucket_acl(
            Bucket=bucket_name,
            ACL="private",  # Set the ACL to private
        )
        print(f"Bucket ACL set to private for {bucket_name}.")

    except ClientError as e:
        print(f"Error: {e}")
        return None


def delete_s3_bucket(session, bucket_name, region=None):
    """
    Delete an S3 bucket and all its contents.

    Parameters:
    - bucket_name: str - Name of the S3 bucket to delete.
    - region: str (optional) - The AWS region where the bucket is located.
    """
    s3 = session.resource("s3", region_name=region)
    bucket = s3.Bucket(bucket_name)

    try:
        # Delete all objects in the bucket
        bucket.objects.all().delete()

        # Delete all object versions (if versioning is enabled)
        bucket.object_versions.all().delete()

        # Delete the bucket itself
        bucket.delete()

        print(f"Bucket '{bucket_name}' and all its contents have been deleted successfully.")
    except Exception as e:
        print(f"Error deleting bucket '{bucket_name}': {e}")
