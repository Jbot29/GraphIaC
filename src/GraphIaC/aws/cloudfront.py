#https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html


import boto3
import json


import time
import boto3
from botocore.exceptions import ClientError

from pydantic import BaseModel
from typing import Optional,List
from gbase import GBase

class CacheBehavior(DefaultCacheBehavior):
    PathPattern: str

class CloudfrontDistribution(GBase):
    distribution_id: str
    arn: str
    
    def exists(self,session):
        cloudfront_client = session.client('cloudfront')
        try:
            # Retrieve the distribution configuration and metadata
            response = cloudfront_client.get_distribution(
                Id=distribution_id
            )
        except cloudfront_client.exceptions.NoSuchDistribution:
            print(f"Distribution with ID '{distribution_id}' does not exist.")
            return False        

        return True

    def create(self,session,G):
        pass

    @classmethod
    def read(self,session,g_id,distribution_id):

        cloudfront_client = session.client('cloudfront')
        try:
            # Retrieve the distribution configuration and metadata
            response = cloudfront_client.get_distribution(
                Id=distribution_id
            )
    
            # Access the distribution configuration and metadata
            distribution = response['Distribution']
    
            # Pretty-print the distribution settings
            print(json.dumps(distribution, indent=4, default=str))


        except cloudfront_client.exceptions.NoSuchDistribution:
            print(f"Distribution with ID '{distribution_id}' does not exist.")
            return False
        except Exception as e:
            print(f"An error occurred: {e}")
            return False


        return CloudfrontDistribution(g_id=g_id,distribution_id=distribution_id,arn=distribution['ARN'])

    
    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass



"""
import boto3
import json

# Initialize the CloudFront client
cloudfront_client = boto3.client('cloudfront')

# Replace with your CloudFront distribution ID
distribution_id = 'YOUR_DISTRIBUTION_ID'

try:
    # Retrieve the distribution configuration and ETag
    response = cloudfront_client.get_distribution_config(Id=distribution_id)
    distribution_config = response['DistributionConfig']
    
    # Access the DefaultCacheBehavior
    default_cache_behavior = distribution_config.get('DefaultCacheBehavior', {})
    
    # Access the CacheBehaviors (additional behaviors)
    cache_behaviors = distribution_config.get('CacheBehaviors', {}).get('Items', [])
    
    # Display the DefaultCacheBehavior
    print("Default Cache Behavior:")
    print(json.dumps(default_cache_behavior, indent=4, default=str))
    print("\n")
    
    # Display each CacheBehavior
    if cache_behaviors:
        print("Additional Cache Behaviors:")
        for idx, behavior in enumerate(cache_behaviors, start=1):
            print(f"Cache Behavior {idx}:")
            print(json.dumps(behavior, indent=4, default=str))
            print("\n")
    else:
        print("No additional Cache Behaviors found.")
        
except cloudfront_client.exceptions.NoSuchDistribution:
    print(f"Distribution with ID '{distribution_id}' does not exist.")
except Exception as e:
    print(f"An error occurred: {e}")


That is great. Working with the straight json for the behavior can lead to errors. Can you help me with a pydantic model for that behaviors. Then we can import from json and export back out to json.
"""



"""
read

import boto3
import json

# Initialize the CloudFront client
cloudfront_client = boto3.client('cloudfront')

# Replace with your CloudFront distribution ID
distribution_id = 'YOUR_DISTRIBUTION_ID'

try:
    # Retrieve the distribution configuration and metadata
    response = cloudfront_client.get_distribution(
        Id=distribution_id
    )
    
    # Access the distribution configuration and metadata
    distribution = response['Distribution']
    
    # Pretty-print the distribution settings
    print(json.dumps(distribution, indent=4, default=str))
    
except cloudfront_client.exceptions.NoSuchDistribution:
    print(f"Distribution with ID '{distribution_id}' does not exist.")
except Exception as e:
    print(f"An error occurred: {e}")

"""

# Initialize clients
#cloudfront_client = boto3.client('cloudfront')
#acm_client = boto3.client('acm')
#s3_client = boto3.client('s3')

# Parameters (replace with your actual values)
#bucket_name = 'your-s3-bucket-name'
#custom_domain = 'your.custom.domain.com'
#certificate_arn = 'arn:aws:acm:us-east-1:123456789012:certificate/your-certificate-id'  # Must be in us-east-1
#distribution_comment = 'CloudFront distribution with custom domain, ACM certificate, and OAC'

def create_oac():
    # Step 1: Create an Origin Access Control (OAC)
    response_oac = cloudfront_client.create_origin_access_control(
        OriginAccessControlConfig={
            'Name': 'OAC-for-' + bucket_name,
            'Description': 'OAC for accessing ' + bucket_name,
            'SigningProtocol': 'sigv4',
            'SigningBehavior': 'always',
            'OriginAccessControlOriginType': 's3'
        }
    )
    oac_id = response_oac['OriginAccessControl']['Id']

    
def create_distribution():
    # Step 2: Create the CloudFront distribution
    response_distribution = cloudfront_client.create_distribution(
        DistributionConfig={
            'CallerReference': 'unique-string-for-distribution',
            'Aliases': {
                'Quantity': 1,
                'Items': [
                    custom_domain
                ]
            },
            'Origins': {
            'Quantity': 1,
            'Items': [
                {
                    'Id': 'S3-' + bucket_name,
                    'DomainName': bucket_name + '.s3.amazonaws.com',
                    'OriginPath': '',
                    'CustomHeaders': {
                        'Quantity': 0
                    },
                    'S3OriginConfig': {
                        'OriginAccessIdentity': ''
                    },
                    'OriginAccessControlId': oac_id
                }
            ]
        },
        'DefaultCacheBehavior': {
            'TargetOriginId': 'S3-' + bucket_name,
            'ViewerProtocolPolicy': 'redirect-to-https',
            'AllowedMethods': {
                'Quantity': 2,
                'Items': ['GET', 'HEAD'],
                'CachedMethods': {
                    'Quantity': 2,
                    'Items': ['GET', 'HEAD']
                }
            },
            'Compress': True,
            'CachePolicyId': '658327ea-f89d-4fab-a63d-7e88639e58f6',  # Use the managed Cache Policy for CachingOptimized
            'OriginRequestPolicyId': '88a5eaf4-2fd4-4709-b370-b4c650ea3fcf'  # Use the managed Origin Request Policy for AllViewer
        },
        'ViewerCertificate': {
            'ACMCertificateArn': certificate_arn,
            'SSLSupportMethod': 'sni-only',
            'MinimumProtocolVersion': 'TLSv1.2_2021',
            'Certificate': certificate_arn,
            'CertificateSource': 'acm'
        },
        'Comment': distribution_comment,
        'Enabled': True,
        'PriceClass': 'PriceClass_All',
        'IsIPV6Enabled': True
    }
    )

    
#distribution_id = response_distribution['Distribution']['Id']
#distribution_domain_name = response_distribution['Distribution']['DomainName']

#print(f"CloudFront distribution '{distribution_id}' created with domain name '{distribution_domain_name}'")
#print(f"OAC ID: {oac_id}")

# Step 3: Update S3 bucket policy to allow access from CloudFront OAC
# Get the CloudFront service principal for your region
#cloudfront_service_principal = 'cloudfront.amazonaws.com'

"""
# Construct the policy
bucket_policy = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "AllowCloudFrontServicePrincipalReadOnly",
            "Effect": "Allow",
            "Principal": {
                "Service": cloudfront_service_principal
            },
            "Action": "s3:GetObject",
            "Resource": f"arn:aws:s3:::{bucket_name}/*",
            "Condition": {
                "StringEquals": {
                    "AWS:SourceArn": f"arn:aws:cloudfront::{boto3.client('sts').get_caller_identity()['Account']}:distribution/{distribution_id}"
                }
            }
        }
    ]
}
"""

## Update the bucket policy
#s3_client.put_bucket_policy(
#    Bucket=bucket_name,
#    Policy=json.dumps(bucket_policy)
#)

#print(f"S3 bucket policy updated to allow access from OAC.")


from pydantic import BaseModel, Field
from typing import List, Optional

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
    FieldLevelEncryptionId: Optional[str] = ''
    RealtimeLogConfigArn: Optional[str] = ''
    CachePolicyId: Optional[str] = None
    OriginRequestPolicyId: Optional[str] = None
    ForwardedValues: Optional[ForwardedValues] = None
    MinTTL: Optional[int] = 0
    DefaultTTL: Optional[int] = 86400
    MaxTTL: Optional[int] = 31536000
    TrustedSigners: Optional[TrustedSigners] = None
    TrustedKeyGroups: Optional[TrustedKeyGroups] = None



class CacheBehaviors(BaseModel):
    Quantity: int
    Items: Optional[List[CacheBehavior]] = None
