from typing import ClassVar, List, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import BaseEdge, BaseNode, VerifyResult

from ..logs import setup_logger

logger = setup_logger()


class CognitoUserPool(BaseNode):
    """A Cognito user pool — the user directory for an app.

    Defaults are locked down for the primary GraphIaC use case (auth in
    front of a small self-hosted UI): self-signup disabled (users are
    created by an admin), email as the sign-in name, and a real password
    minimum. verify() audits exactly those.
    """

    deploy_actions: ClassVar[list] = [
        "cognito-idp:CreateUserPool",
        "cognito-idp:DescribeUserPool",
        "cognito-idp:ListUserPools",
        "cognito-idp:UpdateUserPool",
        "cognito-idp:DeleteUserPool",
        # user management is the deploy identity's job (the walkthroughs
        # and, later, the hosted UI's user admin)
        "cognito-idp:AdminCreateUser",
        "cognito-idp:AdminSetUserPassword",
        "cognito-idp:AdminGetUser",
        "cognito-idp:AdminDeleteUser",
        "cognito-idp:ListUsers",
    ]

    pool_name: str
    region: str = "us-east-2"
    admin_only_signup: bool = True
    password_min_length: int = 12
    pool_id: Optional[str] = None
    arn: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.pool_id or self.pool_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        region = kwargs.get("region") or _region_from_graph(G, g_id, "us-east-2")
        idp = session.client("cognito-idp", region_name=region)
        try:
            pool_id = read_id if read_id and read_id.startswith(f"{region}_") else None
            if not pool_id:
                paginator = idp.get_paginator("list_user_pools")
                for page in paginator.paginate(MaxResults=60):
                    for p in page["UserPools"]:
                        if p["Name"] == read_id:
                            pool_id = p["Id"]
                            break
            if not pool_id:
                return None
            pool = idp.describe_user_pool(UserPoolId=pool_id)["UserPool"]
            admin_cfg = pool.get("AdminCreateUserConfig", {})
            policy = pool.get("Policies", {}).get("PasswordPolicy", {})
            return cls(
                g_id=g_id,
                pool_name=pool["Name"],
                region=region,
                admin_only_signup=admin_cfg.get("AllowAdminCreateUserOnly", False),
                password_min_length=policy.get("MinimumLength", 0),
                pool_id=pool["Id"],
                arn=pool.get("Arn"),
            )
        except ClientError as e:
            logger.error(f"Error reading Cognito user pool {read_id}: {e}")
        return None

    def create(self, session, G):
        idp = session.client("cognito-idp", region_name=self.region)
        try:
            resp = idp.create_user_pool(
                PoolName=self.pool_name,
                AdminCreateUserConfig={"AllowAdminCreateUserOnly": self.admin_only_signup},
                Policies={
                    "PasswordPolicy": {
                        "MinimumLength": self.password_min_length,
                        "RequireUppercase": True,
                        "RequireLowercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": False,
                    }
                },
                UsernameAttributes=["email"],
                AutoVerifiedAttributes=["email"],
                DeletionProtection="ACTIVE",
            )
            self.pool_id = resp["UserPool"]["Id"]
            self.arn = resp["UserPool"].get("Arn")
            logger.info(f"Created Cognito user pool {self.pool_name}: {self.pool_id}")
            return True
        except ClientError as e:
            logger.error(f"Failed to create Cognito user pool {self.pool_name}: {e}")
            raise

    def update(self, session, G, diff=None):
        idp = session.client("cognito-idp", region_name=self.region)
        try:
            idp.update_user_pool(
                UserPoolId=self.pool_id,
                AdminCreateUserConfig={"AllowAdminCreateUserOnly": self.admin_only_signup},
                Policies={
                    "PasswordPolicy": {
                        "MinimumLength": self.password_min_length,
                        "RequireUppercase": True,
                        "RequireLowercase": True,
                        "RequireNumbers": True,
                        "RequireSymbols": False,
                    }
                },
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to update Cognito user pool {self.pool_name}: {e}")
            raise

    def delete(self, session, G):
        if not self.pool_id:
            return
        idp = session.client("cognito-idp", region_name=self.region)
        try:
            idp.update_user_pool(UserPoolId=self.pool_id, DeletionProtection="INACTIVE")
            idp.delete_user_pool(UserPoolId=self.pool_id)
            logger.info(f"Deleted Cognito user pool {self.pool_id}")
        except ClientError as e:
            logger.error(f"Failed to delete Cognito user pool {self.pool_id}: {e}")
            raise

    def verify(self, session, G) -> list:
        live = self.read(session, G, self.g_id, self.read_id, region=self.region)
        if not live:
            return [VerifyResult(name="User pool exists", passed=False,
                                 message=f"no pool named {self.pool_name}")]
        results = [
            VerifyResult(
                name="Self-signup disabled",
                passed=live.admin_only_signup,
                message="users are created by an admin" if live.admin_only_signup
                        else "anyone can sign themselves up",
            ),
            VerifyResult(
                name="Password minimum length",
                passed=live.password_min_length >= 12,
                message=f"{live.password_min_length} chars",
            ),
        ]
        idp = session.client("cognito-idp", region_name=self.region)
        try:
            pool = idp.describe_user_pool(UserPoolId=live.pool_id)["UserPool"]
            protected = pool.get("DeletionProtection") == "ACTIVE"
            results.append(VerifyResult(
                name="Deletion protection",
                passed=protected,
                message="ACTIVE" if protected else "pool can be deleted without a second step",
            ))
        except ClientError as e:
            results.append(VerifyResult(name="Deletion protection", passed=False, message=str(e)))
        return results


class CognitoUserPoolClient(BaseNode):
    """An app client on a user pool. Metadata-only (like ApiEndpoint): the
    CognitoPoolClientEdge does the AWS work, because a client only exists
    on a pool."""

    deploy_actions: ClassVar[list] = [
        "cognito-idp:CreateUserPoolClient",
        "cognito-idp:DescribeUserPoolClient",
        "cognito-idp:ListUserPoolClients",
        "cognito-idp:DeleteUserPoolClient",
    ]

    client_name: str
    callback_urls: List[str] = []
    logout_urls: List[str] = []
    generate_secret: bool = False
    # allow USER_PASSWORD_AUTH — needed when the app itself shows the login
    # form and calls initiate_auth (e.g. a Lambda-hosted UI)
    password_auth: bool = False
    client_id: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.client_name

    @classmethod
    def read(cls, session, G, g_id, read_id, **kwargs):
        return G.nodes[g_id]["data"]


def _region_from_graph(G, g_id, default):
    if G is not None and g_id in G:
        node = G.nodes[g_id].get("data")
        if node is not None and getattr(node, "region", None):
            return node.region
    return default


def _pool_from_graph(session, G, pool_g_id):
    """The connected pool's live pool_id — from the model if create() just
    set it, else from AWS."""
    pool_node = G.nodes[pool_g_id]["data"]
    if pool_node.pool_id:
        return pool_node
    return CognitoUserPool.read(session, G, pool_g_id, pool_node.read_id, region=pool_node.region)


class CognitoPoolClientEdge(BaseEdge):
    """Provisions an app client on a user pool (pool -> client). The edge
    owns the client's auth configuration: SRP + refresh-token flows always;
    OAuth code flow with openid/email scopes when callback URLs are set
    (what a hosted login UI needs)."""

    pool_g_id: str
    client_g_id: str

    @property
    def source_g_id(self):
        return self.pool_g_id

    @property
    def destination_g_id(self):
        return self.client_g_id

    def _find_client(self, session, G):
        pool = _pool_from_graph(session, G, self.pool_g_id)
        if not pool or not pool.pool_id:
            return None, None
        client_node = G.nodes[self.client_g_id]["data"]
        idp = session.client("cognito-idp", region_name=pool.region)
        paginator = idp.get_paginator("list_user_pool_clients")
        for page in paginator.paginate(UserPoolId=pool.pool_id, MaxResults=60):
            for c in page["UserPoolClients"]:
                if c["ClientName"] == client_node.client_name:
                    return pool, c["ClientId"]
        return pool, None

    def read(self, session, G):
        try:
            pool, client_id = self._find_client(session, G)
        except ClientError as e:
            logger.error(f"Error reading user pool client: {e}")
            return None
        if not client_id:
            return None
        G.nodes[self.client_g_id]["data"].client_id = client_id
        return self

    def create(self, session, G):
        pool, client_id = self._find_client(session, G)
        if not pool or not pool.pool_id:
            logger.warning("User pool not available yet; skipping app client")
            return
        client_node = G.nodes[self.client_g_id]["data"]
        if client_id:
            client_node.client_id = client_id
            logger.info(f"App client {client_node.client_name} already exists: {client_id}")
            return True

        idp = session.client("cognito-idp", region_name=pool.region)
        auth_flows = ["ALLOW_USER_SRP_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"]
        if client_node.password_auth:
            auth_flows.append("ALLOW_USER_PASSWORD_AUTH")
        params = {
            "UserPoolId": pool.pool_id,
            "ClientName": client_node.client_name,
            "GenerateSecret": client_node.generate_secret,
            "ExplicitAuthFlows": auth_flows,
            "PreventUserExistenceErrors": "ENABLED",
        }
        if client_node.callback_urls:
            params.update({
                "CallbackURLs": client_node.callback_urls,
                "LogoutURLs": client_node.logout_urls or client_node.callback_urls,
                "AllowedOAuthFlows": ["code"],
                "AllowedOAuthScopes": ["openid", "email"],
                "AllowedOAuthFlowsUserPoolClient": True,
                "SupportedIdentityProviders": ["COGNITO"],
            })
        try:
            resp = idp.create_user_pool_client(**params)
            client_node.client_id = resp["UserPoolClient"]["ClientId"]
            logger.info(
                f"Created app client {client_node.client_name} on {pool.pool_id}: "
                f"{client_node.client_id}"
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to create app client {client_node.client_name}: {e}")
            raise

    def update(self, session, G):
        pass

    def delete(self, session, G):
        try:
            pool, client_id = self._find_client(session, G)
            if pool and client_id:
                idp = session.client("cognito-idp", region_name=pool.region)
                idp.delete_user_pool_client(UserPoolId=pool.pool_id, ClientId=client_id)
                logger.info(f"Deleted app client {client_id}")
        except ClientError as e:
            logger.error(f"Failed to delete app client: {e}")
            raise

    def verify(self, session, G) -> list:
        client_node = G.nodes[self.client_g_id]["data"]
        try:
            pool, client_id = self._find_client(session, G)
        except ClientError as e:
            return [VerifyResult(name="App client", passed=False, message=str(e))]
        if not pool or not client_id:
            return [VerifyResult(name="App client exists", passed=False,
                                 message=f"no client named {client_node.client_name}")]
        results = [VerifyResult(name="App client exists", passed=True, message=client_id)]
        idp = session.client("cognito-idp", region_name=pool.region)
        try:
            c = idp.describe_user_pool_client(UserPoolId=pool.pool_id, ClientId=client_id)[
                "UserPoolClient"
            ]
            hidden = c.get("PreventUserExistenceErrors") == "ENABLED"
            results.append(VerifyResult(
                name="User existence errors hidden",
                passed=hidden,
                message="ENABLED" if hidden else "client leaks which usernames exist",
            ))
        except ClientError as e:
            results.append(VerifyResult(name="App client readable", passed=False, message=str(e)))
        return results


class CognitoLambdaAuthEdge(BaseEdge):
    """Wires Cognito auth into a Lambda (client -> fn): sets the
    COGNITO_POOL_ID / COGNITO_CLIENT_ID / COGNITO_REGION environment
    variables the function's runtime needs to log users in and validate
    their sessions. The pool is discovered through the graph (the client's
    CognitoPoolClientEdge), so the user declares only the arrow."""

    deploy_actions: ClassVar[list] = [
        "cognito-idp:ListUserPoolClients",
        "lambda:GetFunction",
        "lambda:GetFunctionConfiguration",
        "lambda:UpdateFunctionConfiguration",
    ]

    client_g_id: str
    fn_g_id: str

    @property
    def source_g_id(self):
        return self.client_g_id

    @property
    def destination_g_id(self):
        return self.fn_g_id

    def _expected_env(self, session, G):
        """The env vars this edge asserts, or None while pool/client aren't up."""
        pool = None
        for other in G.predecessors(self.client_g_id):
            node = G.nodes[other]["data"]
            if node.__class__.__name__ == "CognitoUserPool":
                pool = _pool_from_graph(session, G, other)
                break
        if not pool or not pool.pool_id:
            return None
        client_node = G.nodes[self.client_g_id]["data"]
        client_id = client_node.client_id
        if not client_id:
            idp = session.client("cognito-idp", region_name=pool.region)
            paginator = idp.get_paginator("list_user_pool_clients")
            for page in paginator.paginate(UserPoolId=pool.pool_id, MaxResults=60):
                for c in page["UserPoolClients"]:
                    if c["ClientName"] == client_node.client_name:
                        client_id = c["ClientId"]
        if not client_id:
            return None
        return {
            "COGNITO_POOL_ID": pool.pool_id,
            "COGNITO_CLIENT_ID": client_id,
            "COGNITO_REGION": pool.region,
        }

    def read(self, session, G):
        fn = G.nodes[self.fn_g_id]["data"]
        try:
            expected = self._expected_env(session, G)
            if not expected:
                return None
            lc = session.client("lambda", region_name=fn.region)
            cfg = lc.get_function_configuration(FunctionName=fn.name)
            env = cfg.get("Environment", {}).get("Variables", {})
            if all(env.get(k) == v for k, v in expected.items()):
                return self
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceNotFoundException":  # no lambda yet
                logger.error(f"Error reading lambda auth env: {e}")
        return None

    def create(self, session, G):
        fn = G.nodes[self.fn_g_id]["data"]
        expected = self._expected_env(session, G)
        if not expected:
            logger.warning("Cognito pool/client not available yet; skipping lambda auth wiring")
            return
        lc = session.client("lambda", region_name=fn.region)
        try:
            lc.get_waiter("function_active_v2").wait(
                FunctionName=fn.name, WaiterConfig={"Delay": 2, "MaxAttempts": 30}
            )
            cfg = lc.get_function_configuration(FunctionName=fn.name)
            env = cfg.get("Environment", {}).get("Variables", {})
            env.update(expected)  # merge — never clobber the function's own env
            lc.get_waiter("function_updated_v2").wait(
                FunctionName=fn.name, WaiterConfig={"Delay": 2, "MaxAttempts": 30}
            )
            lc.update_function_configuration(
                FunctionName=fn.name, Environment={"Variables": env}
            )
            logger.info(f"Wired Cognito auth into {fn.name}: {expected['COGNITO_POOL_ID']}")
            return True
        except ClientError as e:
            logger.error(f"Failed to wire Cognito auth into {fn.name}: {e}")
            raise

    def update(self, session, G):
        return self.create(session, G)

    def delete(self, session, G):
        pass

    def verify(self, session, G) -> list:
        fn = G.nodes[self.fn_g_id]["data"]
        try:
            expected = self._expected_env(session, G)
            if not expected:
                return [VerifyResult(name="Cognito auth wiring", passed=False,
                                     message="pool or app client not found")]
            lc = session.client("lambda", region_name=fn.region)
            cfg = lc.get_function_configuration(FunctionName=fn.name)
            env = cfg.get("Environment", {}).get("Variables", {})
            ok = all(env.get(k) == v for k, v in expected.items())
            return [VerifyResult(
                name="Cognito auth wiring",
                passed=ok,
                message="env matches pool/client" if ok
                        else "lambda env does not match the connected pool/client",
            )]
        except ClientError as e:
            return [VerifyResult(name="Cognito auth wiring", passed=False, message=str(e))]
