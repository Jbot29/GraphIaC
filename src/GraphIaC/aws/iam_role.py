import json
import time
from typing import Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode

from ..logs import setup_logger
from .iam_policy import (
    IamPolicyDocument,
    IamTrustPolicyDocument,
    IamTrustPolicyStatement,
)
from .types import AwsName

logger = setup_logger()

LAMBDA_TRUST_POLICY = IamTrustPolicyDocument(
    Statement=[
        IamTrustPolicyStatement(
            Sid="GraphIaCTrustLambda",
            Effect="Allow",
            Principal={"Service": "lambda.amazonaws.com"},
            Action="sts:AssumeRole",
        )
    ]
)

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

    def exists(self, session):
        if role_exists(session, self.name):
            return True
        return False

    def create(self, session, G):
        # IAM requires a trust policy at creation. Roles in GraphIaC are
        # (so far) always execution roles for Lambda, so that's the default;
        # pass trust_policy explicitly for anything else.
        doc = self.trust_policy or LAMBDA_TRUST_POLICY
        role_arn = role_create(session, self.name, doc)

        if not role_arn:
            return False

        self.arn = role_arn
        return True

    def read(self, session, G, g_id, read_id):
        # cloned = self.copy(deep=True)
        role_arn, policies = role_read(session, self.name)

        if not role_arn:
            return None

        return IAMRole(g_id=self.g_id, name=self.name, trust_policy=policies, arn=role_arn)

    def update(self, session, G):
        pass

    def delete(self, session, G):
        delete_iam_role(session, self.name)

    def diff(self, session, G, diff_object):
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

    def read(self, session):
        pass

    def create(self, session, G):
        pass

    def update(self, session, G):
        pass

    def delete(self, session, G):
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


def role_exists(session, role_name):
    iam_client = session.client("iam")
    try:
        # Check if the role already exists
        iam_client.get_role(RoleName=role_name)
    except iam_client.exceptions.NoSuchEntityException:
        return False

    return True


def role_create(session, role_name, policy_document, wait=True, max_wait_seconds=30):
    iam_client = session.client("iam")

    if isinstance(policy_document, IamTrustPolicyDocument):
        policy_document = policy_document.model_dump(exclude_none=True)

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


def role_read(session, role_name):
    iam_client = session.client("iam")

    try:
        # Fetch the IAM role details using the role name
        response = iam_client.get_role(RoleName=role_name)

        # Extract the policy and ARN from the response
        role_arn = response["Role"]["Arn"]

        # Fetch the policies attached to this role
        policies_response = iam_client.list_attached_role_policies(RoleName=role_name)
        policies = {
            policy["PolicyName"]: policy["PolicyArn"]
            for policy in policies_response["AttachedPolicies"]
        }

        # Create an IAMRole instance with the fetched details
        return role_arn, policies

    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":  # not-created-yet is normal
            logger.error(f"Error reading role {role_name}: {e}")
        return None, None


def role_has_policy(session, role_name, policy_arn):
    iam_client = session.client("iam")
    # List all attached policies for the given role
    response = iam_client.list_attached_role_policies(RoleName=role_name)

    # Extract the ARNs of the attached policies
    attached_policies = [policy["PolicyArn"] for policy in response["AttachedPolicies"]]

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


def delete_iam_role(session, role_name: str):
    iam = session.client("iam")
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


def attach_role_policy(session, role_name: str, policy_arn) -> None:
    iam = session.client("iam")

    iam.attach_role_policy(
        RoleName=role_name,
        PolicyArn=policy_arn,
    )
