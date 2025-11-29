import boto3
import time
import json
from botocore.exceptions import ClientError
from pydantic import BaseModel,constr
from typing import Optional,List

from .types import AwsName

class IAMRole(BaseModel):
    g_id: str
    name: AwsName
    policy: dict
    arn: Optional[str] = None    

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    def exists(self,session):
        print(f"{self.__class__.__name__}: Exists {self}")
        if role_exists(session,self.name):
            return True
        return False 

    def create(self,session,G):
        print(f"{self.__class__.__name__}: Create {self}")
        role_arn = role_create(session,self.name,self.policy)

        if not role_arn:
            return False

        self.arn = role_arn
        return True


    def read(self,session,G,g_id,read_id):
        #cloned = self.copy(deep=True)
        role_arn,policies = role_read(session,self.name)
        print("READ IAM ROLE",role_arn,policies)
        if not role_arn:
            return None
        
        return IAMRole(g_id=self.g_id, name=self.name, policy=policies, arn=role_arn)
    
    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass

    def diff(self,session,G,diff_object):
        return False
    

class IAMRolePolicyEdge(BaseModel):
    role_g_id: str
    node_g_id: str
    policy_arn: str

    def exists(self,session):
        pass

    def create(self,session,G):
        pass

    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass



    
def role_exists(session,role_name):
    iam_client = session.client('iam')
    try:
        # Check if the role already exists
        role_response = iam_client.get_role(RoleName=role_name)
        print(f"Role '{role_name}' already exists.")
        role_arn = role_response['Role']['Arn']
    except iam_client.exceptions.NoSuchEntityException:
        return False

    return True

def role_create(session,role_name,policy_document):
    iam_client = session.client('iam')

    create_role_response = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(policy_document),
        Description='Role for Lambda execution',
    )
    role_arn = create_role_response['Role']['Arn']
    return role_arn



def role_read(session,role_name):
    iam_client = session.client('iam')    

    try:
        # Fetch the IAM role details using the role name
        response = iam_client.get_role(RoleName=role_name)

        # Extract the policy and ARN from the response
        role_arn = response["Role"]["Arn"]

        # Fetch the policies attached to this role
        policies_response = iam_client.list_attached_role_policies(RoleName=role_name)
        policies = {policy["PolicyName"]: policy["PolicyArn"] for policy in policies_response["AttachedPolicies"]}
        
        # Create an IAMRole instance with the fetched details
        return role_arn,policies

    except ClientError as e:
        # Handle error if role doesn't exist or if there's an issue
        print(f"Error: {e}")
        return None,None



def role_has_policy(session,role_name,policy_arn):
    iam_client = session.client('iam')
    # List all attached policies for the given role
    response = iam_client.list_attached_role_policies(RoleName=role_name)
    
    # Extract the ARNs of the attached policies
    attached_policies = [policy['PolicyArn'] for policy in response['AttachedPolicies']]
    
    # Check if the given policy ARN is already attached
    return policy_arn in attached_policies    
    
#
"""
    # Attach a basic execution policy to the role
    print(f"Attaching AWSLambdaBasicExecutionRole policy to {role_name}")
    iam_client.attach_role_policy(
        RoleName=role_name,
        PolicyArn="arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
    )    
    pass

"""
