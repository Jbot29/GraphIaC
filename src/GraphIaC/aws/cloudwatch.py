import os
import zipfile
import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel,constr,Field
from typing import Optional,List
from ..models import BaseNode

from .types import AwsName
from .iam_role import IAMRolePolicyEdge
from .iam_policy import IamTrustPolicyStatement,get_trust_policy_for_role


