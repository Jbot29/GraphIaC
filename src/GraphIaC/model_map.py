
from GraphIaC.aws.route53 import HostedZone
from GraphIaC.aws.certificate import Certificate,CertificateHostedZoneEdge,get_dns_validation
from GraphIaC.aws.s3 import S3Bucket
from GraphIaC.aws.iam_role import IAMRole
from GraphIaC.aws.ec2_sg import SecurityGroup
from GraphIaC.aws.ec2.alb import ALB
from GraphIaC.aws.ec2.listener import Listener

from GraphIaC.aws.dynamodb import DynamoTable

from GraphIaC.aws.lambda_func import LambdaZipFile

from GraphIaC.aws.apigateway import ApiSite,ApiEndpoint

#todo find a better way to do this


BASE_MODEL_MAP = {
    "HostedZone": HostedZone,
    "Certificate": Certificate,
    "CertificateHostedZoneEdge": CertificateHostedZoneEdge,
    "S3Bucket": S3Bucket,
    "IAMRole": IAMRole,
    "LambdaZipFile": LambdaZipFile,
    "SecurityGroup":SecurityGroup,
    "ALB": ALB,
    "Listener": Listener,
    "DynamoTable": DynamoTable,
    "ApiSite": ApiSite,
    "ApiEndpoint": ApiEndpoint,
    
}

"""
def register_model(cls: Type[BaseModel]) -> Type[BaseModel]:
    MODEL_REGISTRY[cls.__name__] = cls
    return cls

# Define a common base class for convenience
@register_model

"""
