"""Generate the DSL type registry consumed by the JavaScript parser.

The registry is the single source of truth that keeps the two DSL parsers
(JS for the live editor, Python for the engine) in sync on AWS knowledge:
every node type's fields, defaults, and name field; every edge type's
endpoint types, endpoint g_id field names, and canonical direction.

Node/edge fields are introspected from the Pydantic models in model_map.py.
Two things Pydantic cannot know are declared here by hand:

  - NAME_FIELDS: which field is the node's human-chosen AWS name (the DSL
    defaults it to the label)
  - EDGE_ENDPOINTS: which node types an edge connects, and via which fields
    (the DSL's `a -> b` inference table)

Regenerate after changing any model:

    python -m GraphIaC.dsl_registry
"""

import json
from pathlib import Path

from GraphIaC.logs import setup_logger
from GraphIaC.model_map import BASE_MODEL_MAP
from GraphIaC.models import BaseEdge, BaseNode

logger = setup_logger()

VERSION = "0.1"

# node type -> the field the label defaults into (see dsl/spec.md "Name defaulting")
NAME_FIELDS = {
    "S3Bucket": "bucket_name",
    "DynamoTable": "table_name",
    "IAMRole": "name",
    "LambdaZipFile": "name",
    "ApiSite": "site_name",
    "ApiEndpoint": "endpoint_name",
    "CloudFrontFunction": "name",
    "ALB": "name",
}

# edge type -> (source type, source g_id field, dest type, dest g_id field)
# This is the `a -> b` inference table: one edge per unordered node-type pair.
EDGE_ENDPOINTS = {
    "ACMCertificateHostedZoneEdge": ("ACMCertificate", "cert_g_id", "HostedZone", "hz_g_id"),
    "CloudFrontS3OACEdge": ("CloudFrontDistribution", "cf_g_id", "S3Bucket", "s3_g_id"),
    "CloudFrontRoute53Edge": ("CloudFrontDistribution", "cf_g_id", "HostedZone", "hz_g_id"),
    "CloudFrontFunctionEdge": ("CloudFrontFunction", "fn_g_id", "CloudFrontDistribution", "cf_g_id"),
    "SiteEndpointEdge": ("ApiSite", "site_node_g_id", "ApiEndpoint", "endpoint_node_g_id"),
    "EndpointLambdaEdge": ("ApiEndpoint", "endpoint_node_g_id", "LambdaZipFile", "lambda_node_g_id"),
    "IAMRolePolicyLambdaEdge": ("IAMRole", "role_g_id", "LambdaZipFile", "node_g_id"),
    "LambdaDynamoEdge": ("LambdaZipFile", "lambda_node_g_id", "DynamoTable", "dynamo_node_g_id"),
    "SESDomainRoute53Edge": ("SESDomainIdentity", "ses_g_id", "HostedZone", "zone_g_id"),
    "LambdaSESEdge": ("LambdaZipFile", "lambda_node_g_id", "SESDomainIdentity", "ses_node_g_id"),
}

REGISTRY_JS_PATH = Path(__file__).parent / "web" / "registry.js"

HEADER = """\
/* GENERATED FILE — do not edit.
 * The DSL type registry: node fields/defaults/name fields and the edge
 * inference table, introspected from the GraphIaC Pydantic models.
 * Regenerate with:  python -m GraphIaC.dsl_registry
 */
(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.GraphIaCRegistry = api;
})(typeof self !== "undefined" ? self : this, function () {
"use strict";
return """


def _field_info(model_cls):
    """Introspect a Pydantic model's fields into plain JSON-able metadata.

    Fields are sorted so both parsers iterate (and so error-emit) in the
    same order — the generated registry.js is JSON-dumped with sorted keys.
    """
    fields = {}
    for fname, f in sorted(model_cls.model_fields.items()):
        if fname == "g_id":
            continue  # set from the label, never a DSL arg
        info = {"required": f.is_required()}
        if not f.is_required():
            default = f.get_default(call_default_factory=True)
            try:
                json.dumps(default)
                info["default"] = default
            except (TypeError, ValueError):
                info["default"] = None
        fields[fname] = info
    return fields


def build_registry():
    nodes, edges = {}, {}
    for type_name, cls in sorted(BASE_MODEL_MAP.items()):
        if issubclass(cls, BaseNode):
            nodes[type_name] = {
                "nameField": NAME_FIELDS.get(type_name),
                "fields": _field_info(cls),
            }
        elif issubclass(cls, BaseEdge):
            if type_name not in EDGE_ENDPOINTS:
                logger.debug(f"edge {type_name} has no EDGE_ENDPOINTS entry — not usable from the DSL")
                continue
            src_type, src_field, dst_type, dst_field = EDGE_ENDPOINTS[type_name]
            edges[type_name] = {
                "source": {"type": src_type, "field": src_field},
                "dest": {"type": dst_type, "field": dst_field},
                "fields": _field_info(cls),
            }
    for edge_name in EDGE_ENDPOINTS:
        if edge_name not in edges:
            logger.warning(f"EDGE_ENDPOINTS entry {edge_name} is not in BASE_MODEL_MAP")
    return {"version": VERSION, "nodes": nodes, "edges": edges}


def write_registry_js(path=REGISTRY_JS_PATH):
    registry = build_registry()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(registry, indent=2, sort_keys=True)
    path.write_text(HEADER + body + ";\n});\n")
    logger.info(f"wrote {path} ({len(registry['nodes'])} node types, {len(registry['edges'])} edge types)")
    return registry


if __name__ == "__main__":
    write_registry_js()
