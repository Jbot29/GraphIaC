import boto3
import time
import json
from botocore.exceptions import ClientError
from pydantic import BaseModel,constr,Field,AliasChoices
from typing import Optional,List
from GraphIaC.models import BaseNode,BaseEdge

from .types import AwsName

from .iam_policy import IamPolicyDocument,IamTrustPolicyDocument
"""
IAM Role
│
├── Trust Policy (1 per role)
│     - who can assume the role?
│     - lambda.amazonaws.com must be here for Lambdas
│
├── Inline Policies (0..many)
│     - precise, granular permissions
│     - perfect for GraphIaC edges
│
└── Managed Policies (0..many)
      - prebuilt or reusable

"""


class IAMRole(BaseNode):
    g_id: str
    name: AwsName
    trust_policy: Optional[IamTrustPolicyDocument] = None
    inline_policy: Optional[IamPolicyDocument] = None
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
        role_arn = role_create(session,self.name,self.trust_policy)

        if not role_arn:
            return False

        self.arn = role_arn
        return True


    def read(self,session,G,g_id,read_id):
        #cloned = self.copy(deep=True)
        role_arn,policies = role_read(session,self.name)

        if not role_arn:
            return None
        
        return IAMRole(g_id=self.g_id, name=self.name, trust_policy=policies, arn=role_arn)
    
    def update(self,session,G):
        pass

    def delete(self,session,G):
        delete_iam_role(session,self.name)

    def diff(self,session,G,diff_object):
        return False
    

class IAMRolePolicyEdge(BaseEdge):
    role_g_id: str 
    node_g_id: Optional[str] = None

    policy_arn: Optional[str] = None

    @property
    def policy_name(self) -> str:
        return f"IAMRolePolicyEdge-{self.role_g_id}-{self.node_g_id}"
    
    @property
    def source_g_id(self) -> str:
        return self.role_g_id
    
    @property
    def destination_g_id(self) -> str:
        return self.node_g_id
    
    def read(self,session):
        pass

    def create(self,session,G):
        pass

    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass

class IAMRoleInlinePolicyEdge(BaseEdge):
    role_g_id: str 
    
    @property
    def policy_name(self) -> str:
        return f"IAMRolePolicyEdge-{self.source_g_id}-{self.destination_g_id}"
    
    @property
    def source_g_id(self):
        return None
    
    @property
    def destination_g_id(self):
        return None



    
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

"""
def role_create(session,role_name,policy_document):
    iam_client = session.client('iam')

    create_role_response = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(policy_document),
        Description='Role for Lambda execution',
    )
    role_arn = create_role_response['Role']['Arn']
    return role_arn
"""

def role_create(session, role_name, policy_document, wait=True, max_wait_seconds=30):
    iam_client = session.client("iam")

    create_role_response = iam_client.create_role(
        RoleName=role_name,
        AssumeRolePolicyDocument=json.dumps(policy_document),
        Description="Role for Lambda execution",
    )
    role_arn = create_role_response["Role"]["Arn"]

    if wait:
        # First: wait until the role is visible
        iam_client.get_waiter("role_exists").wait(RoleName=role_name)

        # Then: give IAM a bit of time to propagate the trust policy
        # and be assumable by Lambda
        deadline = time.time() + max_wait_seconds
        while True:
            try:
                # Dumb-but-effective: just call get_role in a loop.
                # If IAM returns it without errors, assume it's propagated enough.
                iam_client.get_role(RoleName=role_name)
                break  # good enough
            except ClientError:
                if time.time() >= deadline:
                    raise
                time.sleep(2)  # small backoff

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


def delete_iam_role(session,role_name: str):
    iam = session.client('iam')
    # 1. Detach managed policies
    attached = iam.list_attached_role_policies(RoleName=role_name)
    for p in attached.get("AttachedPolicies", []):
        print(f"Detaching managed policy: {p['PolicyArn']}")
        iam.detach_role_policy(RoleName=role_name, PolicyArn=p["PolicyArn"])

    # 2. Delete inline policies
    inline = iam.list_role_policies(RoleName=role_name)
    for policy_name in inline.get("PolicyNames", []):
        print(f"Deleting inline policy: {policy_name}")
        iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

    # 3. Remove from instance profiles
    profiles = iam.list_instance_profiles_for_role(RoleName=role_name)
    for profile in profiles.get("InstanceProfiles", []):
        profile_name = profile["InstanceProfileName"]
        print(f"Removing role from instance profile: {profile_name}")
        iam.remove_role_from_instance_profile(
            InstanceProfileName=profile_name,
            RoleName=role_name,
        )

    # 4. Delete the role
    print(f"Deleting role: {role_name}")
    iam.delete_role(RoleName=role_name)

    print("Done.")

# Usage:

