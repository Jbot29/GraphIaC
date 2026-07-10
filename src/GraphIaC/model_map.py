from GraphIaC.aws.apigateway import ApiEndpoint, ApiSite, EndpointLambdaEdge, SiteEndpointEdge
from GraphIaC.aws.certificate import ACMCertificate, ACMCertificateHostedZoneEdge
from GraphIaC.aws.cloudfront import (
    ACMCertificateCloudFrontEdge,
    CloudFrontDistribution,
    CloudFrontFunction,
    CloudFrontFunctionEdge,
    CloudFrontRoute53Edge,
    CloudFrontS3OACEdge,
)
from GraphIaC.aws.cognito import (
    CognitoLambdaAuthEdge,
    CognitoPoolClientEdge,
    CognitoUserPool,
    CognitoUserPoolClient,
)
from GraphIaC.aws.dynamodb import DynamoTable
from GraphIaC.aws.ec2.alb import ALB
from GraphIaC.aws.ec2.listener import Listener
from GraphIaC.aws.ec2_sg import SecurityGroup
from GraphIaC.aws.iam_role import IAMRole, IAMRoleInlinePolicyEdge, IAMRolePolicyEdge
from GraphIaC.aws.lambda_dynamodb import LambdaDynamoEdge
from GraphIaC.aws.lambda_func import IAMRolePolicyLambdaEdge, LambdaZipFile
from GraphIaC.aws.route53 import HostedZone, Route53AliasRecord
from GraphIaC.aws.s3 import S3Bucket
from GraphIaC.aws.ses import LambdaSESEdge, SESDomainIdentity, SESDomainRoute53Edge

# todo find a better way to do this


BASE_MODEL_MAP = {
    "HostedZone": HostedZone,
    "Route53AliasRecord": Route53AliasRecord,
    "ACMCertificate": ACMCertificate,
    "ACMCertificateHostedZoneEdge": ACMCertificateHostedZoneEdge,
    "ACMCertificateCloudFrontEdge": ACMCertificateCloudFrontEdge,
    "S3Bucket": S3Bucket,
    "CloudFrontDistribution": CloudFrontDistribution,
    "CloudFrontFunction": CloudFrontFunction,
    "CloudFrontFunctionEdge": CloudFrontFunctionEdge,
    "CloudFrontS3OACEdge": CloudFrontS3OACEdge,
    "CloudFrontRoute53Edge": CloudFrontRoute53Edge,
    "IAMRole": IAMRole,
    "IAMRolePolicyEdge": IAMRolePolicyEdge,
    "IAMRoleInlinePolicyEdge": IAMRoleInlinePolicyEdge,
    "IAMRolePolicyLambdaEdge": IAMRolePolicyLambdaEdge,
    "LambdaZipFile": LambdaZipFile,
    "SiteEndpointEdge": SiteEndpointEdge,
    "EndpointLambdaEdge": EndpointLambdaEdge,
    "SecurityGroup": SecurityGroup,
    "ALB": ALB,
    "Listener": Listener,
    "CognitoUserPool": CognitoUserPool,
    "CognitoUserPoolClient": CognitoUserPoolClient,
    "CognitoPoolClientEdge": CognitoPoolClientEdge,
    "CognitoLambdaAuthEdge": CognitoLambdaAuthEdge,
    "DynamoTable": DynamoTable,
    "LambdaDynamoEdge": LambdaDynamoEdge,
    "ApiSite": ApiSite,
    "ApiEndpoint": ApiEndpoint,
    "SESDomainIdentity": SESDomainIdentity,
    "SESDomainRoute53Edge": SESDomainRoute53Edge,
    "LambdaSESEdge": LambdaSESEdge,
}

"""
def register_model(cls: Type[BaseModel]) -> Type[BaseModel]:
    MODEL_REGISTRY[cls.__name__] = cls
    return cls

# Define a common base class for convenience
@register_model

"""
