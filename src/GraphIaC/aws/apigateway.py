
import boto3

from pydantic import BaseModel
from typing import Optional,List
from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode


from typing import Optional, Literal, Dict
from pydantic import BaseModel, Field, validator
import boto3
from botocore.exceptions import ClientError
from GraphIaC.models import BaseNode,BaseEdge

from typing import Optional, Literal

import boto3
from pydantic import BaseModel

from ..logs import setup_logger

logger = setup_logger()

class ApiSite(BaseNode):
    site_name: str 
    protocol: Literal["HTTP"] = "HTTP"
    stage: str = "$default"
    base_path: str = "/"    # for future nesting, versioning, etc.
    region: str = "us-east-2"
    
    @property
    def read_id(self) -> Optional[str]:

        return self.site_name

    @classmethod
    def read(self,session,G,g_id,read_id,region="us-east-2"):

        return get_api_site(session, g_id,read_id, region)


    def create(self,session,G):
        resp = create_api_site(session, self)
        return True
        
    

class ApiEndpoint(BaseNode):
    """ This node is weird in that it doesn't actually write anything to aws and is metadata only, the edge updates aws """
    endpoint_name: str 
    path: str          
    method: Literal["GET", "POST", "PUT", "DELETE", "OPTIONS"]

    @property
    def read_id(self) -> Optional[str]:
        print(f"W: {self.method}")
        return _route_key_for_endpoint(self.path,self.method)        

    

    @classmethod
    def read(self,session,G,g_id,read_id,region="us-east-2"):

        return G.nodes[g_id]['data']



class SiteEndpointEdge(BaseEdge):
    site_node_g_id: str
    endpoint_node_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.site_node_g_id
    
    @property
    def destination_g_id(self) -> str:
        return self.endpoint_node_g_id

        
    def read(self,session,G):
        site = G.nodes[self.site_node_g_id]['data']
        endpoint = G.nodes[self.endpoint_node_g_id]['data']
        return endpoint_exists_on_site(session,site,endpoint)

    def create(self,session,G):

        site = G.nodes[self.site_node_g_id]['data']
        endpoint = G.nodes[self.endpoint_node_g_id]['data']
        
        attach_endpoint_to_site(
            session,
            site,
            endpoint)

        return True

class EndpointLambdaEdge(BaseEdge):
    endpoint_node_g_id: str
    lambda_node_g_id: str

    @property
    def source_g_id(self) -> str:
        return self.endpoint_node_g_id
    
    @property
    def destination_g_id(self) -> str:
        return self.lambda_node_g_id

    def read(self,session,G):
        lambda_node = G.nodes[self.lambda_node_g_id]['data']
        endpoint = G.nodes[self.endpoint_node_g_id]['data']

    def create(self,session,G):
        lambda_node = G.nodes[self.lambda_node_g_id]['data']
        endpoint = G.nodes[self.endpoint_node_g_id]['data']


def _find_api_by_name(client, name: str) -> Optional[dict]:
    """
    HTTP APIs don't have a 'get_by_name', so we scan get_apis().
    Assumes Name is unique within the account/region.
    """
    paginator = client.get_paginator("get_apis")
    for page in paginator.paginate():
        for api in page.get("Items", []):
            if api.get("Name") == name:
                return api
    return None


def _ensure_stage(client, api_id: str, stage_name: str) -> None:
    """
    Make sure a stage exists for this API. If not, create it with AutoDeploy.
    """
    # get_stage will throw NotFoundException if stage doesn't exist
    try:
        client.get_stage(ApiId=api_id, StageName=stage_name)
        return
    except client.exceptions.NotFoundException:
        pass

    client.create_stage(
        ApiId=api_id,
        StageName=stage_name,
        AutoDeploy=True,
    )


# --- public CRUD helpers -----------------------------------------------------


def create_api_site(session: boto3.session.Session, site: ApiSite) -> dict:
    """
    Create a new HTTP API + stage for this ApiSite.

    Returns the raw create_api response.
    """

    client = session.client("apigatewayv2",region_name=site.region)

    resp = client.create_api(
        Name=site.site_name,
        ProtocolType=site.protocol,
        # this is the usual route selection expression for HTTP APIs
        RouteSelectionExpression="$request.method $request.path",
    )

    api_id = resp["ApiId"]
    _ensure_stage(client, api_id, site.stage)

    return resp


def get_api_site(session: boto3.session.Session,g_id, name: str,region) -> Optional[ApiSite]:
    """
    Look up an ApiSite by its name. Returns None if it doesn't exist.
    """

    client = session.client("apigatewayv2",region_name=region)
    api = _find_api_by_name(client, name)
    if not api:
        print(f"API GATEWAY NOT FOUND {name}")
        return None

    # Try to infer the "main" stage: prefer the requested name, else $default.
    stage_name = "$default"
    stages_resp = client.get_stages(ApiId=api["ApiId"])
    stages = stages_resp.get("Items", [])
    if any(s.get("StageName") == stage_name for s in stages):
        pass  # keep "$default"
    elif stages:
        # fall back to the first stage if $default isn't there
        stage_name = stages[0].get("StageName", "$default")

    return ApiSite(
        g_id=g_id,
        site_name=api["Name"],
        protocol=api["ProtocolType"],
        stage_name=stage_name,
    )


def update_api_site(session: boto3.session.Session, site: ApiSite) -> dict:
    """
    Update basic properties of an existing ApiSite.
    Right now we only touch Name and ensure the stage exists.
    """
    client = session.client("apigatewayv2",region_name=site.region)    

    api = _find_api_by_name(client, site.name)
    if not api:
        raise ValueError(f"ApiSite {site.name!r} does not exist")

    api_id = api["ApiId"]

    # For now, just make sure name & protocol are correct.
    # (HTTP API currently only supports HTTP, so protocol is mostly fixed.)
    resp = client.update_api(
        ApiId=api_id,
        Name=site.name,
        # ProtocolType can't be changed once created; we don't send it here.
        # You can add CorsConfiguration, Description, etc. later.
    )

    _ensure_stage(client, api_id, site.stage)

    return resp


def delete_api_site(session: boto3.session.Session, name: str) -> bool:
    """
    Delete an ApiSite (HTTP API) by name.

    Returns True if deleted, False if it didn't exist.
    """
    client = session.client("apigatewayv2",region_name=site.region)    


    api = _find_api_by_name(client, name)
    if not api:
        return False

    client.delete_api(ApiId=api["ApiId"])
    return True


def upsert_api_site(session: boto3.session.Session, site: ApiSite) -> dict:
    """
    Idempotent: create if missing, otherwise update.

    Returns the underlying create_api / update_api response.
    """
    client = _apigw(session)

    existing = _find_api_by_name(client, site.name)
    if not existing:
        return create_api_site(session, site)

    api_id = existing["ApiId"]

    resp = client.update_api(
        ApiId=api_id,
        Name=site.name,
    )

    _ensure_stage(client, api_id, site.stage)

    return resp


def _route_key_for_endpoint(path,method) -> str:
    """
    API Gateway HTTP API route keys look like: 'POST /newsletter'
    """
    # Just in case someone passes in "post" etc.
    method = method.upper()
    # Ensure leading slash
    path = path if path.startswith("/") else f"/{path}"
    return f"{method} {path}"


def _find_route_by_key(client, api_id: str, route_key: str) -> Optional[dict]:
    paginator = client.get_paginator("get_routes")
    for page in paginator.paginate(ApiId=api_id):
        for route in page.get("Items", []):
            if route.get("RouteKey") == route_key:
                return route
    return None



def attach_endpoint_to_site(
    session: boto3.session.Session,
    site: ApiSite,
    endpoint: ApiEndpoint,
) -> dict:
    """
    Idempotently attach an ApiEndpoint to an ApiSite.

    In AWS terms: ensure a Route exists on the HTTP API with the right method + path.
    Does NOT create an integration yet (that will be the Endpoint→Lambda edge).
    """
    client = session.client("apigatewayv2",region_name=site.region)

    api = _find_api_by_name(client, site.site_name)
    if not api:
        raise ValueError(f"ApiSite {site_name!r} does not exist")

    api_id = api["ApiId"]
    route_key = _route_key_for_endpoint(endpoint.path,endpoint.method)

    existing = _find_route_by_key(client, api_id, route_key)
    if existing:
        # For now we don't try to update anything; just return the existing route.
        return existing

    resp = client.create_route(
        ApiId=api_id,
        RouteKey=route_key,
        # For now: open access, no auth, no API key
        AuthorizationType="NONE",
    )

    return resp


def detach_endpoint_from_site(
    session: boto3.session.Session,
    site_name: str,
    endpoint: ApiEndpoint,
) -> bool:
    """
    Remove the route that corresponds to this endpoint from the given site.

    Returns True if deleted, False if it didn't exist.
    """

    client = session.client("apigatewayv2",region_name=site.region)
    
    api = _find_api_by_name(client, site_name)
    if not api:
        # nothing to do – site itself doesn't exist
        return False

    api_id = api["ApiId"]
    route_key = _route_key_for_endpoint(endpoint)

    existing = _find_route_by_key(client, api_id, route_key)
    if not existing:
        return False

    client.delete_route(ApiId=api_id, RouteId=existing["RouteId"])
    return True

def endpoint_exists_on_site(
    session: boto3.session.Session,
    site: ApiSite,
    endpoint: ApiEndpoint,
) -> bool:
    
    client = session.client("apigatewayv2",region_name=site.region)

    api = _find_api_by_name(client, site.site_name)
    if not api:
        return False  # Site doesn't exist → route can't exist.

    api_id = api["ApiId"]
    route_key = _route_key_for_endpoint(endpoint.path,endpoint.method)

    paginator = client.get_paginator("get_routes")
    for page in paginator.paginate(ApiId=api_id):
        for route in page.get("Items", []):
            if route.get("RouteKey") == route_key:
                return True

    return False


def api_route_to_endpoint(route: dict) -> ApiEndpoint:
    """
    Convert an API Gateway HTTP API route into an ApiEndpoint model.
    """
    route_key = route["RouteKey"]  # e.g. "POST /newsletter"
    
    # Split route key
    try:
        method, path = route_key.split(" ", 1)
    except ValueError:
        raise ValueError(f"Invalid RouteKey format: {route_key!r}")

    # Ensure path always starts with "/"
    if not path.startswith("/"):
        path = "/" + path

    # Generate a stable endpoint name
    # Example: POST /newsletter → "POST_newsletter"
    endpoint_name = f"{method}_{path.lstrip('/').replace('/', '_')}"

    return ApiEndpoint(
        endpoint_name=endpoint_name,
        method=method,
        path=path,
    )

def get_endpoint_from_site(
    session: boto3.session.Session,
    site_name: str,
    method: str,
    path: str,
) -> Optional[ApiEndpoint]:
    client = _apigw(session)

    api = _find_api_by_name(client, site_name)
    if not api:
        return None

    api_id = api["ApiId"]
    route_key = f"{method.upper()} {path}"

    route = _find_route_by_key(client, api_id, route_key)
    if not route:
        return None

    return api_route_to_endpoint(route)

def _find_integration_for_lambda(client, api_id: str, lambda_arn: str) -> Optional[dict]:
    """
    Try to locate an existing integration that targets the given lambda ARN.
    """
    paginator = client.get_paginator("get_integrations")
    for page in paginator.paginate(ApiId=api_id):
        for integ in page.get("Items", []):
            if integ.get("IntegrationUri") == lambda_arn and integ.get("IntegrationType") == "AWS_PROXY":
                return integ
    return None


def _api_execution_arn(region: str, account_id: str, api_id: str) -> str:
    """
    Execution ARN prefix used in Lambda permissions.
    Format: arn:aws:execute-api:{region}:{account}:{apiId}
    """
    return f"arn:aws:execute-api:{region}:{account_id}:{api_id}"


def _get_account_id(session: boto3.session.Session) -> str:
    return session.client("sts").get_caller_identity()["Account"]


# --- core CRUD for the edge --------------------------------------------------

def attach_route_to_lambda(
    session: boto3.session.Session,
    site_name: str,
    method: str,
    path: str,
    lambda_function_name: str,
) -> dict:
    """
    Ensure the route (method+path) on the given site invokes the given Lambda.

    Returns a dict with api_id, route_id, integration_id, lambda_arn.
    """
    apigw = session.client("apigatewayv2",region_name=site.region)
    lam = session.client("lambda",region_name=site.region)

    api = _find_api_by_name(apigw, site_name)
    if not api:
        raise ValueError(f"ApiSite {site_name!r} does not exist")

    api_id = api["ApiId"]
    rk = _route_key(method, path)

    route = _find_route_by_key(apigw, api_id, rk)
    if not route:
        raise ValueError(f"Route {rk!r} does not exist on site {site_name!r}. Create the endpoint/route first.")

    # Resolve Lambda ARN
    fn = lam.get_function(FunctionName=lambda_function_name)
    lambda_arn = fn["Configuration"]["FunctionArn"]

    # 1) Ensure integration exists
    integration = _find_integration_for_lambda(apigw, api_id, lambda_arn)
    if not integration:
        integration = apigw.create_integration(
            ApiId=api_id,
            IntegrationType="AWS_PROXY",
            IntegrationUri=lambda_arn,
            PayloadFormatVersion="2.0",
            TimeoutInMillis=30000,
        )

    integration_id = integration["IntegrationId"]

    # 2) Point route to integration
    target = f"integrations/{integration_id}"
    if route.get("Target") != target:
        apigw.update_route(
            ApiId=api_id,
            RouteId=route["RouteId"],
            Target=target,
        )

    # 3) Add Lambda permission for API Gateway invocation (idempotent-ish)
    # Use a stable StatementId so repeated applies are safe.
    region = session.region_name or boto3.session.Session().region_name
    account_id = _get_account_id(session)

    statement_id = f"apigw-{api_id}-{method.lower()}-{path.strip('/').replace('/', '-') or 'root'}"
    source_arn = f"{_api_execution_arn(region, account_id, api_id)}/*/{method.upper()}{path if path.startswith('/') else '/' + path}"

    try:
        lam.add_permission(
            FunctionName=lambda_function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal="apigateway.amazonaws.com",
            SourceArn=source_arn,
        )
    except lam.exceptions.ResourceConflictException:
        # Permission with same StatementId already exists — fine for idempotency.
        pass

    return {
        "api_id": api_id,
        "route_id": route["RouteId"],
        "integration_id": integration_id,
        "lambda_arn": lambda_arn,
        "route_key": rk,
        "lambda_permission_statement_id": statement_id,
        "lambda_permission_source_arn": source_arn,
    }


def get_route_lambda_attachment(
    session: boto3.session.Session,
    site_name: str,
    method: str,
    path: str,
) -> Optional[dict]:
    """
    Read back the current attachment state:
    - route exists?
    - does it have a Target?
    - what integration does it point to?
    - what is that integration uri?
    """
    apigw = _apigw(session)

    api = _find_api_by_name(apigw, site_name)
    if not api:
        return None

    api_id = api["ApiId"]
    rk = _route_key(method, path)

    route = _find_route_by_key(apigw, api_id, rk)
    if not route:
        return None

    target = route.get("Target")
    if not target or not target.startswith("integrations/"):
        return {
            "api_id": api_id,
            "route_key": rk,
            "route": route,
            "attached": False,
        }

    integration_id = target.split("/", 1)[1]
    integration = apigw.get_integration(ApiId=api_id, IntegrationId=integration_id)

    return {
        "api_id": api_id,
        "route_key": rk,
        "route": route,
        "attached": True,
        "integration": integration,
        "integration_id": integration_id,
        "integration_uri": integration.get("IntegrationUri"),
    }


def update_route_lambda_attachment(
    session: boto3.session.Session,
    site_name: str,
    method: str,
    path: str,
    lambda_function_name: str,
) -> dict:
    """
    For GraphIaC this is basically the same as attach: ensure the attachment
    points to the desired lambda. Kept as a separate name for CRUD symmetry.
    """
    return attach_route_to_lambda(session, site_name, method, path, lambda_function_name)


def detach_route_from_lambda(
    session: boto3.session.Session,
    site_name: str,
    method: str,
    path: str,
    *,
    delete_integration: bool = False,
    remove_lambda_permission: bool = False,
    lambda_function_name_for_permission: Optional[str] = None,
) -> bool:
    """
    Detach a route from any integration by clearing Target.

    Optionally:
    - delete the referenced integration (only if you are sure it's not shared)
    - remove the lambda permission statement (requires function name)
    """

    apigw = session.client("apigatewayv2",region_name=site.region)

    api = _find_api_by_name(apigw, site_name)
    if not api:
        return False

    api_id = api["ApiId"]
    rk = _route_key(method, path)

    route = _find_route_by_key(apigw, api_id, rk)
    if not route:
        return False

    target = route.get("Target")
    integration_id = None
    if target and target.startswith("integrations/"):
        integration_id = target.split("/", 1)[1]

    # Clear target (detach)
    apigw.update_route(ApiId=api_id, RouteId=route["RouteId"], Target="")

    # Optional: delete integration (careful — integrations can be shared)
    if delete_integration and integration_id:
        try:
            apigw.delete_integration(ApiId=api_id, IntegrationId=integration_id)
        except apigw.exceptions.NotFoundException:
            pass

    # Optional: remove lambda permission
    if remove_lambda_permission:
        if not lambda_function_name_for_permission:
            raise ValueError("lambda_function_name_for_permission is required when remove_lambda_permission=True")

        lam = _lambda(session)
        statement_id = f"apigw-{api_id}-{method.lower()}-{path.strip('/').replace('/', '-') or 'root'}"
        try:
            lam.remove_permission(FunctionName=lambda_function_name_for_permission, StatementId=statement_id)
        except lam.exceptions.ResourceNotFoundException:
            pass

    return True
