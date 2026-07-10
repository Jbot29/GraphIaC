"""Generate the deploy identity's IAM policy from the graph.

Every node/edge class declares `deploy_actions` — the IAM actions its
read/create/update/delete/verify calls need. This module unions those
declarations, so the minimal deploy policy for an infra file is derived
from the file itself: the types in your graph ARE the permission list.
(Same introspection pattern as the DSL registry.)

Actions-first, v1: statements are grouped per service for readability,
Resource "*" — resource-level scoping is a future refinement.

    python -m GraphIaC <profile> --infra_file site.giac policy
    python -m GraphIaC <profile> policy --all
"""

import json
from collections import defaultdict

from GraphIaC.model_map import BASE_MODEL_MAP

# actions the engine itself relies on regardless of graph contents
BASE_ACTIONS = ["sts:GetCallerIdentity"]


def actions_for_types(type_names):
    """The union of deploy_actions for the given registered type names."""
    actions = set(BASE_ACTIONS)
    unknown = []
    for name in type_names:
        cls = BASE_MODEL_MAP.get(name)
        if cls is None:
            unknown.append(name)
            continue
        actions.update(cls.deploy_actions)
    if unknown:
        raise KeyError(f"unregistered types: {', '.join(sorted(unknown))}")
    return sorted(actions)


def types_in_graph(graph):
    """Every node and edge type in a parsed DSL graph."""
    return sorted(
        {n["type"] for n in graph["nodes"]} | {e["type"] for e in graph["edges"]}
    )


def policy_document(actions):
    """Actions -> an IAM policy dict, one statement per service prefix."""
    by_service = defaultdict(list)
    for a in actions:
        by_service[a.split(":")[0]].append(a)
    statements = [
        {
            "Sid": f"GraphIaC{service.replace('-', '').capitalize()}",
            "Effect": "Allow",
            "Action": sorted(acts),
            "Resource": "*",
        }
        for service, acts in sorted(by_service.items())
    ]
    return {"Version": "2012-10-17", "Statement": statements}


def policy_for_graph(graph):
    """The minimal deploy policy for a parsed DSL graph."""
    return policy_document(actions_for_types(types_in_graph(graph)))


def policy_for_all():
    """The full-catalog policy: every registered type's actions."""
    return policy_document(actions_for_types(BASE_MODEL_MAP.keys()))


def render(policy, role_name="graphiac-deploy"):
    """The policy JSON plus the command that applies it."""
    doc = json.dumps(policy, indent=2)
    return (
        f"{doc}\n\n"
        f"# Apply it to your deploy role (with an admin-ish profile):\n"
        f"#   aws iam put-role-policy --role-name {role_name} \\\n"
        f"#       --policy-name GraphIaCDeployPolicy \\\n"
        f"#       --policy-document file://<saved-policy>.json\n"
    )
