import json
from typing import List, Optional

from pydantic import BaseModel

from GraphIaC.models import BaseNode


class CloudfrontDistribution(BaseNode):
    distribution_id: str
    arn: str

    def exists(self, session):
        cloudfront_client = session.client("cloudfront")
        try:
            # Retrieve the distribution configuration and metadata
            cloudfront_client.get_distribution(Id=self.distribution_id)
        except cloudfront_client.exceptions.NoSuchDistribution:
            print(f"Distribution with ID '{self.distribution_id}' does not exist.")
            return False

        return True

    def create(self, session, G):
        pass

    @classmethod
    def read(self, session, g_id, distribution_id):
        cloudfront_client = session.client("cloudfront")
        try:
            # Retrieve the distribution configuration and metadata
            response = cloudfront_client.get_distribution(Id=distribution_id)

            # Access the distribution configuration and metadata
            distribution = response["Distribution"]

            # Pretty-print the distribution settings
            print(json.dumps(distribution, indent=4, default=str))

        except cloudfront_client.exceptions.NoSuchDistribution:
            print(f"Distribution with ID '{distribution_id}' does not exist.")
            return False
        except Exception as e:
            print(f"An error occurred: {e}")
            return False

        return CloudfrontDistribution(
            g_id=g_id, distribution_id=distribution_id, arn=distribution["ARN"]
        )

    def update(self, session, G):
        pass

    def delete(self, session, G):
        pass


def create_oac(session, bucket_name):
    cloudfront_client = session.client("cloudfront")
    # Step 1: Create an Origin Access Control (OAC)
    response_oac = cloudfront_client.create_origin_access_control(
        OriginAccessControlConfig={
            "Name": "OAC-for-" + bucket_name,
            "Description": "OAC for accessing " + bucket_name,
            "SigningProtocol": "sigv4",
            "SigningBehavior": "always",
            "OriginAccessControlOriginType": "s3",
        }
    )
    return response_oac["OriginAccessControl"]["Id"]


def create_distribution(
    session, bucket_name, custom_domain, oac_id, certificate_arn, distribution_comment
):
    cloudfront_client = session.client("cloudfront")
    # Step 2: Create the CloudFront distribution
    cloudfront_client.create_distribution(
        DistributionConfig={
            "CallerReference": "unique-string-for-distribution",
            "Aliases": {"Quantity": 1, "Items": [custom_domain]},
            "Origins": {
                "Quantity": 1,
                "Items": [
                    {
                        "Id": "S3-" + bucket_name,
                        "DomainName": bucket_name + ".s3.amazonaws.com",
                        "OriginPath": "",
                        "CustomHeaders": {"Quantity": 0},
                        "S3OriginConfig": {"OriginAccessIdentity": ""},
                        "OriginAccessControlId": oac_id,
                    }
                ],
            },
            "DefaultCacheBehavior": {
                "TargetOriginId": "S3-" + bucket_name,
                "ViewerProtocolPolicy": "redirect-to-https",
                "AllowedMethods": {
                    "Quantity": 2,
                    "Items": ["GET", "HEAD"],
                    "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]},
                },
                "Compress": True,
                "CachePolicyId": "658327ea-f89d-4fab-a63d-7e88639e58f6",  # Use the managed Cache Policy for CachingOptimized
                "OriginRequestPolicyId": "88a5eaf4-2fd4-4709-b370-b4c650ea3fcf",  # Use the managed Origin Request Policy for AllViewer
            },
            "ViewerCertificate": {
                "ACMCertificateArn": certificate_arn,
                "SSLSupportMethod": "sni-only",
                "MinimumProtocolVersion": "TLSv1.2_2021",
                "Certificate": certificate_arn,
                "CertificateSource": "acm",
            },
            "Comment": distribution_comment,
            "Enabled": True,
            "PriceClass": "PriceClass_All",
            "IsIPV6Enabled": True,
        }
    )


class TrustedSigners(BaseModel):
    Enabled: bool
    Quantity: int
    Items: Optional[List[str]] = None


class TrustedKeyGroups(BaseModel):
    Enabled: bool
    Quantity: int
    Items: Optional[List[str]] = None


class LambdaFunctionAssociation(BaseModel):
    LambdaFunctionARN: str
    EventType: str
    IncludeBody: Optional[bool] = False


class LambdaFunctionAssociations(BaseModel):
    Quantity: int
    Items: Optional[List[LambdaFunctionAssociation]] = None


class FunctionAssociation(BaseModel):
    FunctionARN: str
    EventType: str


class FunctionAssociations(BaseModel):
    Quantity: int
    Items: Optional[List[FunctionAssociation]] = None


class CachedMethods(BaseModel):
    Quantity: int
    Items: List[str]


class AllowedMethods(BaseModel):
    Quantity: int
    Items: List[str]
    CachedMethods: Optional[CachedMethods] = None


class ForwardedValues(BaseModel):
    QueryString: bool
    Cookies: dict
    Headers: dict
    QueryStringCacheKeys: dict


class DefaultCacheBehavior(BaseModel):
    TargetOriginId: str
    ViewerProtocolPolicy: str
    AllowedMethods: AllowedMethods
    SmoothStreaming: Optional[bool] = False
    Compress: Optional[bool] = False
    LambdaFunctionAssociations: Optional[LambdaFunctionAssociations] = None
    FunctionAssociations: Optional[FunctionAssociations] = None
    FieldLevelEncryptionId: Optional[str] = ""
    RealtimeLogConfigArn: Optional[str] = ""
    CachePolicyId: Optional[str] = None
    OriginRequestPolicyId: Optional[str] = None
    ForwardedValues: Optional[ForwardedValues] = None
    MinTTL: Optional[int] = 0
    DefaultTTL: Optional[int] = 86400
    MaxTTL: Optional[int] = 31536000
    TrustedSigners: Optional[TrustedSigners] = None
    TrustedKeyGroups: Optional[TrustedKeyGroups] = None


class CacheBehavior(DefaultCacheBehavior):
    PathPattern: str


class CacheBehaviors(BaseModel):
    Quantity: int
    Items: Optional[List[CacheBehavior]] = None
