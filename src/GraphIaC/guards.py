"""Guards — the `?` statements: safety invariants declared in the language.

    ? private(bucket)
    ? https-only(cf)
    ? locked-to(bucket, cf)

A guard is an INDEPENDENT check. The node and edge classes try their best
to provision safely; guards prove it with different code. The rule that
keeps them honest:

    Predicates read AWS through raw boto3 only. They may take names and
    ids from the parsed graph, but they must never import or call the
    node/edge classes' read()/create()/verify() — shared vocabulary,
    separate implementation.

Guards warn; they never block. `verify` counts a failing guard as a
failure (exit 1 for CI); `run` prints guard results after applying but
exits 0 regardless — GraphIaC never wedges you out of your own account.
(A --strict mode that blocks is a possible future flag.)

Evaluation states: pass, fail, and pending (the target doesn't exist yet
— e.g. BLOCKED behind a certificate — so there is nothing to audit).
"""

from typing import List

from botocore.exceptions import ClientError
from pydantic import BaseModel

from .logs import setup_logger

logger = setup_logger()


class GuardResult(BaseModel):
    predicate: str
    args: List[str]
    status: str  # "pass" | "fail" | "pending"
    message: str = ""

    @property
    def label(self) -> str:
        return f"? {self.predicate}({', '.join(self.args)})"


# predicate name -> the node types its arguments must have, in order.
# Generated into the DSL registry so BOTH parsers validate guards at
# parse time (unknown predicate / wrong target type = editor feedback).
PREDICATES = {
    "private": {
        "args": ["S3Bucket"],
        "doc": "the bucket blocks all public access and its policy grants none",
    },
    "https-only": {
        "args": ["CloudFrontDistribution"],
        "doc": "viewers are forced to HTTPS with modern TLS",
    },
    "locked-to": {
        "args": ["S3Bucket", "CloudFrontDistribution"],
        "doc": "only that distribution can read the bucket",
    },
    "admin-only-signup": {
        "args": ["CognitoUserPool"],
        "doc": "nobody can sign themselves up",
    },
    "authed": {
        "args": ["LambdaZipFile"],
        "doc": "if the function has a public URL, Cognito auth is wired into it",
    },
}


def _field(nodes, g_id, name):
    """A field value from the parsed graph; None when unset or an
    unresolved $ref (which makes the guard pending)."""
    val = nodes[g_id]["fields"].get(name)
    if isinstance(val, dict) and "$ref" in val:
        return None
    return val


def _pending(reason):
    return ("pending", reason)


def _check_private(session, nodes, bucket_id):
    import json

    name = _field(nodes, bucket_id, "bucket_name")
    if not name:
        return _pending("bucket name not resolvable yet")
    s3 = session.client("s3")
    try:
        s3.head_bucket(Bucket=name)
    except ClientError:
        return _pending("bucket not created yet")

    try:
        cfg = s3.get_public_access_block(Bucket=name)["PublicAccessBlockConfiguration"]
    except ClientError:
        return ("fail", "no public access block configured")
    for key in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets"):
        if not cfg.get(key):
            return ("fail", f"public access block: {key} is off")

    try:
        policy = json.loads(s3.get_bucket_policy(Bucket=name)["Policy"])
        for stmt in policy.get("Statement", []):
            if stmt.get("Effect") != "Allow":
                continue
            principal = stmt.get("Principal")
            if principal == "*" or principal == {"AWS": "*"}:
                if not stmt.get("Condition"):
                    return ("fail", "bucket policy grants unconditional public access")
    except ClientError:
        pass  # no policy at all is fine — nothing granted

    return ("pass", "public access blocked, no public grants")


def _get_distribution(session, nodes, cf_id):
    """The live distribution config for a CloudFrontDistribution node, by
    id when pinned, else by alias — raw boto3, no class code."""
    cf = session.client("cloudfront")
    dist_id = _field(nodes, cf_id, "distribution_id")
    if not dist_id:
        domain = _field(nodes, cf_id, "domain_name")
        if not domain:
            return None, None
        paginator = cf.get_paginator("list_distributions")
        for page in paginator.paginate():
            for d in page.get("DistributionList", {}).get("Items", []):
                if domain in d.get("Aliases", {}).get("Items", []):
                    dist_id = d["Id"]
                    break
    if not dist_id:
        return None, None
    try:
        resp = cf.get_distribution(Id=dist_id)["Distribution"]
        return resp, resp["DistributionConfig"]
    except ClientError:
        return None, None


def _check_https_only(session, nodes, cf_id):
    dist, config = _get_distribution(session, nodes, cf_id)
    if not config:
        return _pending("distribution not created yet")
    vpp = config.get("DefaultCacheBehavior", {}).get("ViewerProtocolPolicy", "")
    if vpp not in ("redirect-to-https", "https-only"):
        return ("fail", f"viewer protocol policy is '{vpp}'")
    tls = config.get("ViewerCertificate", {}).get("MinimumProtocolVersion", "")
    if tls < "TLSv1.2_2021":
        return ("fail", f"minimum TLS is '{tls}'")
    return ("pass", f"{vpp}, {tls}")


def _check_locked_to(session, nodes, bucket_id, cf_id):
    import json

    name = _field(nodes, bucket_id, "bucket_name")
    if not name:
        return _pending("bucket name not resolvable yet")
    dist, _ = _get_distribution(session, nodes, cf_id)
    if not dist:
        return _pending("distribution not created yet")

    s3 = session.client("s3")
    try:
        policy = json.loads(s3.get_bucket_policy(Bucket=name)["Policy"])
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchBucket", "404"):
            return _pending("bucket not created yet")
        return ("fail", "bucket has no policy — nothing restricts reads to the distribution")

    cf_scoped = False
    for stmt in policy.get("Statement", []):
        if stmt.get("Effect") != "Allow":
            continue
        if stmt.get("Principal", {}) == {"Service": "cloudfront.amazonaws.com"}:
            source = stmt.get("Condition", {}).get("StringEquals", {}).get("AWS:SourceArn", "")
            if source == dist["ARN"]:
                cf_scoped = True
            else:
                return ("fail", f"policy trusts a different distribution: {source}")
        else:
            return ("fail", "policy grants access beyond the distribution")
    if not cf_scoped:
        return ("fail", "no statement scoping reads to the distribution")
    return ("pass", f"only {dist['Id']} can read")


def _check_admin_only_signup(session, nodes, pool_id):
    name = _field(nodes, pool_id, "pool_name")
    region = _field(nodes, pool_id, "region") or "us-east-2"
    if not name:
        return _pending("pool name not resolvable yet")
    idp = session.client("cognito-idp", region_name=region)
    target = None
    paginator = idp.get_paginator("list_user_pools")
    for page in paginator.paginate(MaxResults=60):
        for p in page["UserPools"]:
            if p["Name"] == name:
                target = p["Id"]
    if not target:
        return _pending("user pool not created yet")
    pool = idp.describe_user_pool(UserPoolId=target)["UserPool"]
    if pool.get("AdminCreateUserConfig", {}).get("AllowAdminCreateUserOnly"):
        return ("pass", "self-signup disabled")
    return ("fail", "anyone can sign themselves up")


def _check_authed(session, nodes, fn_id):
    name = _field(nodes, fn_id, "name")
    region = _field(nodes, fn_id, "region") or "us-east-2"
    if not name:
        return _pending("function name not resolvable yet")
    lc = session.client("lambda", region_name=region)
    try:
        url_cfg = lc.get_function_url_config(FunctionName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            try:
                lc.get_function_configuration(FunctionName=name)
            except ClientError:
                return _pending("function not created yet")
            return ("pass", "no public URL — nothing exposed")
        raise
    if url_cfg.get("AuthType") == "AWS_IAM":
        return ("pass", "URL requires IAM auth")
    env = lc.get_function_configuration(FunctionName=name).get("Environment", {}).get(
        "Variables", {}
    )
    if env.get("COGNITO_POOL_ID") and env.get("COGNITO_CLIENT_ID"):
        return ("pass", "public URL with Cognito auth wired in")
    return ("fail", "PUBLIC URL WITH NO AUTH — anyone on the internet can call this")


_CHECKS = {
    "private": _check_private,
    "https-only": _check_https_only,
    "locked-to": _check_locked_to,
    "admin-only-signup": _check_admin_only_signup,
    "authed": _check_authed,
}


def evaluate(session, graph) -> List[GuardResult]:
    """Evaluate every `?` guard in a parsed graph against live AWS."""
    nodes = {n["g_id"]: n for n in graph["nodes"]}
    results = []
    for guard in graph.get("guards", []):
        check = _CHECKS[guard["predicate"]]
        try:
            status, message = check(session, nodes, *guard["args"])
        except ClientError as e:
            status, message = "fail", f"could not evaluate: {e.response['Error']['Code']}"
        results.append(GuardResult(predicate=guard["predicate"], args=guard["args"],
                                   status=status, message=message))
    return results


def report(results, printer=None) -> int:
    """Log guard results; returns the number of failures."""
    symbols = {"pass": "✓", "fail": "✗", "pending": "…"}
    failed = 0
    for r in results:
        line = f"  {symbols[r.status]} {r.label}" + (f": {r.message}" if r.message else "")
        if r.status == "fail":
            failed += 1
            logger.warning(line)
        else:
            logger.info(line)
    return failed


def registry_entry() -> dict:
    """The predicate signatures, for the generated DSL registry."""
    return {name: {"args": spec["args"], "doc": spec["doc"]} for name, spec in PREDICATES.items()}
