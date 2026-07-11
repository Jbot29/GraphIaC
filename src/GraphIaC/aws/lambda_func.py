import os
import time
from typing import ClassVar, Dict, Optional

from botocore.exceptions import ClientError
from pydantic import Field

from ..logs import setup_logger
from ..models import BaseNode
from .iam_policy import (
    IamTrustPolicyStatement,
    get_trust_policy_for_role,
    upsert_trust_statement_for_role,
)
from .iam_role import IAMRolePolicyEdge, attach_role_policy
from .types import AwsName

logger = setup_logger()


def _lambda_trusted(doc):
    """Does any statement already let lambda.amazonaws.com assume the role?"""
    for s in doc.Statement:
        if s.Effect != "Allow":
            continue
        svc = s.Principal.get("Service")
        services = [svc] if isinstance(svc, str) else (svc or [])
        actions = [s.Action] if isinstance(s.Action, str) else s.Action
        if "lambda.amazonaws.com" in services and "sts:AssumeRole" in actions:
            return True
    return False

# TODO: Zipfile compare sha

assume_role_policy_document = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

# NB: IAM statement Sids must be strictly alphanumeric — real AWS rejects
# punctuation (moto doesn't validate this)
stmt = IamTrustPolicyStatement(
    Sid="GraphIaCTrustLambda",
    Effect="Allow",
    Principal={"Service": "lambda.amazonaws.com"},
    Action="sts:AssumeRole",
)


class IAMRolePolicyLambdaEdge(IAMRolePolicyEdge):
    deploy_actions: ClassVar[list] = [
        "iam:AttachRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:GetRole",
        "iam:UpdateAssumeRolePolicy",
    ]

    policy_arn: str = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"

    def read(self, session, G):
        role_name = G.nodes[self.role_g_id]["data"].read_id
        iam = session.client("iam")
        try:
            paginator = iam.get_paginator("list_attached_role_policies")
            for page in paginator.paginate(RoleName=role_name):
                for p in page["AttachedPolicies"]:
                    if p["PolicyArn"] == self.policy_arn:
                        return self
        except ClientError as e:
            if e.response["Error"]["Code"] != "NoSuchEntity":  # no role yet = not attached
                logger.error(f"Error reading attached policies for {role_name}: {e}")
        return None

    def create(self, session, G):
        role_name = G.nodes[self.role_g_id]["data"].read_id
        attach_role_policy(session, role_name, self.policy_arn)

        # an imported role may not trust Lambda yet — assert it (roles
        # GraphIaC creates already have it, so this is usually a no-op)
        if not _lambda_trusted(get_trust_policy_for_role(session, role_name)):
            upsert_trust_statement_for_role(session, role_name, stmt)
            logger.info(f"Added Lambda trust to role {role_name}")

        return True

    def update(self, session, G):
        pass

    def delete(self, session, G):
        pass


class LambdaZipFile(BaseNode):
    deploy_actions: ClassVar[list] = [
        "lambda:CreateFunction",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionCode",
        "lambda:UpdateFunctionConfiguration",
        "lambda:DeleteFunction",
        "lambda:CreateFunctionUrlConfig",
        "lambda:GetFunctionUrlConfig",
        "lambda:AddPermission",
        "iam:PassRole",  # create_function hands the execution role to Lambda
    ]

    name: AwsName
    region: str = "us-east-2"
    runtime: str
    handler: str
    zip_file_path: str
    description: Optional[str] = Field("No description", description="A description of the lambda")
    timeout: Optional[int] = 15
    memory_size: Optional[int] = 128
    publish: Optional[bool] = True
    # public_url=True manages a Lambda function URL (AuthType NONE + public
    # invoke permission — put auth inside the function, e.g. via
    # CognitoLambdaAuthEdge). False leaves any existing URL alone.
    public_url: bool = False
    url: Optional[str] = None
    # environment variables asserted on the function (merged — edges like
    # CognitoLambdaAuthEdge add theirs alongside, nothing clobbers)
    env: Dict[str, str] = {}

    @property
    def read_id(self) -> Optional[str]:
        return self.name

    def exists(self, session):
        if lambda_exists(session, self.name, self.region):
            return True
        return False

    def create(self, session, G):
        role_edge = None

        incoming_edges = G.in_edges(self.g_id)
        for ie in incoming_edges:
            edge = G[ie[0]][ie[1]]
            edge_data = edge["data"]
            if isinstance(edge_data, IAMRolePolicyLambdaEdge):
                role_edge = edge_data

        iam_role = G.nodes[role_edge.role_g_id]["data"]

        # The role must trust Lambda BEFORE create_function — and the edge
        # that owns that trust runs after nodes. An imported role (e.g.
        # graphiac-deploy, which starts with account-root trust only) would
        # otherwise fail "cannot be assumed" forever. Assert it here — and
        # let failures surface: without trust, create can only fail anyway.
        if not _lambda_trusted(get_trust_policy_for_role(session, iam_role.read_id)):
            upsert_trust_statement_for_role(session, iam_role.read_id, stmt)
            logger.info(f"Added Lambda trust to role {iam_role.read_id}")

        result = lambda_create(
            session,
            self.name,
            self.runtime,
            iam_role.arn,
            self.handler,
            self.description,
            self.timeout,
            self.memory_size,
            self.publish,
            self.zip_file_path,
            self.region,
            env=self.env,
        )
        if self.public_url:
            self.url = ensure_function_url(session, self.name, self.region)
        return result

    def read(self, session, G, g_id, read_id):
        # cloned = self.copy(deep=True)
        response = lambda_read(session, self.name, self.region)
        if not response:
            return None

        current_config = LambdaZipFile(
            g_id=self.g_id,  # Or store separately if AWS doesn't have this
            name=self.name,  # The name won't change
            runtime=response.get("Runtime"),
            handler=response.get("Handler"),
            zip_file_path=self.zip_file_path,  # Not tracked by AWS
            description=response.get("Description"),
            timeout=response.get("Timeout"),
            memory_size=response.get("MemorySize"),
            publish=self.publish,  # AWS doesn't store this boolean
            public_url=self.public_url,  # managed only when True (no drift signal)
            url=read_function_url(session, self.name, self.region),
            env=self.env,  # asserted by merge in update; echoed here
        )

        return current_config

    def update(self, session, G):
        result = lambda_update(session, self, self.region)
        if self.public_url:
            self.url = ensure_function_url(session, self.name, self.region)
        return result

    def delete(self, session, G):
        pass

    def diff(self, session, G, diff_object):
        return True


def lambda_exists(session, function_name, region):
    lambda_client = session.client("lambda", region_name=region)
    try:
        # Attempt to retrieve the Lambda function configuration
        lambda_client.get_function(FunctionName=function_name)
        return True  # Function exists
    except ClientError as e:
        # Check for the 'ResourceNotFoundException'
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            return False  # Function doesn't exist
        else:
            raise  # Reraise the exception if it's not a 'ResourceNotFoundException'


def lambda_create(
    session,
    function_name,
    runtime,
    role_arn,
    handler,
    description,
    timeout,
    memory_size,
    publish,
    zip_file_name,
    region,
    env=None,
):
    lambda_client = session.client("lambda", region_name=region)
    # Read zip file bytes
    with open(zip_file_name, "rb") as f:
        zip_bytes = f.read()

    logger.info(f"Creating Lambda function '{function_name}' ({len(zip_bytes)} bytes)...")

    # A just-created IAM role can take tens of seconds to propagate to the
    # Lambda service — get_role succeeding does NOT mean Lambda can assume
    # it yet. Retry the specific "cannot be assumed" rejection.
    params = {}
    if env:
        params["Environment"] = {"Variables": dict(env)}

    deadline = time.time() + 90
    while True:
        try:
            lambda_client.create_function(
                FunctionName=function_name,
                Runtime=runtime,
                Role=role_arn,  # The ARN of the IAM role
                Handler=handler,
                Code={"ZipFile": zip_bytes},
                Description=description,
                Timeout=timeout,
                MemorySize=memory_size,
                Publish=publish,
                **params,
            )
            return
        except ClientError as e:
            retriable = (
                e.response["Error"]["Code"] == "InvalidParameterValueException"
                and "cannot be assumed" in e.response["Error"].get("Message", "")
            )
            if not retriable or time.time() >= deadline:
                raise
            logger.info("  waiting for the IAM role to propagate to Lambda...")
            time.sleep(3)


def ensure_function_url(session, function_name, region):
    """A public function URL (AuthType NONE) + the invoke permission that
    makes it reachable. Idempotent.

    Two grants are required (AWS change, October 2025): InvokeFunctionUrl
    for the URL itself, and InvokeFunction scoped to URL calls via the
    lambda:InvokedViaFunctionUrl condition. Missing either = 403 Forbidden.
    Permissions FIRST, then the URL config.
    """
    lc = session.client("lambda", region_name=region)
    grants = [
        {
            "StatementId": "FunctionURLAllowPublicAccess",
            "Action": "lambda:InvokeFunctionUrl",
            "Principal": "*",
            "FunctionUrlAuthType": "NONE",
        },
        {
            "StatementId": "FunctionURLInvokeAllowPublicAccess",
            "Action": "lambda:InvokeFunction",
            "Principal": "*",
            "InvokedViaFunctionUrl": True,
        },
    ]
    for grant in grants:
        try:
            lc.add_permission(FunctionName=function_name, **grant)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceConflictException":  # already granted
                raise
    try:
        resp = lc.get_function_url_config(FunctionName=function_name)
    except lc.exceptions.ResourceNotFoundException:
        resp = lc.create_function_url_config(FunctionName=function_name, AuthType="NONE")
        logger.info(f"Created function URL for {function_name}: {resp['FunctionUrl']}")
    return resp["FunctionUrl"]


def read_function_url(session, function_name, region):
    lc = session.client("lambda", region_name=region)
    try:
        return lc.get_function_url_config(FunctionName=function_name)["FunctionUrl"]
    except lc.exceptions.ResourceNotFoundException:
        return None


def lambda_read(session, func_name, region):
    lambda_client = session.client("lambda", region_name=region)

    try:
        # Get the current AWS configuration for this Lambda function
        response = lambda_client.get_function_configuration(FunctionName=func_name)

        # Build a new model using AWS's current settings;
        # For fields that AWS doesn't store (zip_file_path, publish),
        # we keep the local model's values.

        return response

    except lambda_client.exceptions.ResourceNotFoundException:
        # If AWS cannot find the Lambda by that name, return None
        return None
    except Exception as e:
        # In a real-world scenario, handle or log the exception
        print(f"An error occurred: {e}")
        return None


def lambda_update(session, lambda_config, region_name):
    logger.debug(f"lambda_update: {region_name}")
    lambda_client = session.client("lambda", region_name=region_name)

    function_name = lambda_config.name

    # Prepare a response dictionary describing what (if anything) we changed
    result = {"updated_config": False, "updated_code": False, "error": None}

    # 1. Check if the function exists and retrieve current configuration
    try:
        current = lambda_client.get_function_configuration(FunctionName=function_name)
    except lambda_client.exceptions.ResourceNotFoundException:
        result["error"] = f"Lambda function '{function_name}' does not exist."
        return result
    except ClientError as e:
        result["error"] = f"Unexpected error accessing Lambda: {e}"
        return result

    logger.debug(f"current config: {current}")
    # 2. Compare AWS config to local config. We'll build an update dict dynamically.
    config_updates = {}

    if current.get("Runtime") != lambda_config.runtime:
        config_updates["Runtime"] = lambda_config.runtime

    if current.get("Handler") != lambda_config.handler:
        config_updates["Handler"] = lambda_config.handler

    if current.get("Description") != lambda_config.description:
        config_updates["Description"] = lambda_config.description

    if current.get("Timeout") != lambda_config.timeout:
        config_updates["Timeout"] = lambda_config.timeout

    if current.get("MemorySize") != lambda_config.memory_size:
        config_updates["MemorySize"] = lambda_config.memory_size

    # env is asserted by MERGE: our declared vars win, everything else
    # (e.g. CognitoLambdaAuthEdge's COGNITO_*) is preserved
    if lambda_config.env:
        live_env = current.get("Environment", {}).get("Variables", {})
        merged = {**live_env, **lambda_config.env}
        if merged != live_env:
            config_updates["Environment"] = {"Variables": merged}

    # 3. Update function configuration if needed (then wait — a config
    #    update in flight makes the code update below conflict)
    if config_updates:
        try:
            lambda_client.update_function_configuration(FunctionName=function_name, **config_updates)
            lambda_client.get_waiter("function_updated_v2").wait(
                FunctionName=function_name, WaiterConfig={"Delay": 2, "MaxAttempts": 30}
            )
            logger.info(f"Updated {function_name} config: {', '.join(config_updates)}")
            result["updated_config"] = True
        except ClientError as e:
            result["error"] = f"Failed to update Lambda config: {e}"
            return result

    # 4. Re-upload code (always — keeps code in sync with the local zip)
    zip_path = lambda_config.zip_file_path
    if not os.path.isfile(zip_path):
        result["error"] = f"Zip file does not exist: {zip_path}"
        return result

    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    if len(zip_bytes) == 0:
        result["error"] = f"Zip file is empty: {zip_path}"
        return result

    logger.info(f"Updating {function_name} code ({len(zip_bytes)} bytes)")
    lambda_client.update_function_code(
        FunctionName=function_name, ZipFile=zip_bytes, Publish=lambda_config.publish
    )
    result["updated_code"] = True

    return result
