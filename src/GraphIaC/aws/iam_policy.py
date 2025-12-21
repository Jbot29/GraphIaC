from typing import Any, Literal, List
from pydantic import BaseModel
import json
import urllib.parse


class IamPolicyStatement(BaseModel):
    Sid: str
    Effect: Literal["Allow", "Deny"] = "Allow"
    Action: List[str] | str
    Resource: List[str] | str | None = None   # <-- optional now
    Condition: dict[str, Any] | None = None    

class IamPolicyDocument(BaseModel):
    Version: str = "2012-10-17"
    Statement: List[IamPolicyStatement] = []




def put_inline_policy_for_role(
    session,
    role_name: str,
    policy_name: str,
    policy_doc: IamPolicyDocument,
) -> None:
    """
    Create or replace an inline policy on the given role using the provided
    IamPolicyDocument.
    """
    iam = session.client("iam")

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=policy_doc.model_dump_json(exclude_none=True),
    )


def get_inline_policy_for_role(
    session,
    role_name: str,
    policy_name: str,
) -> IamPolicyDocument | None:
    """
    Fetch an inline policy for the given role by name.
    Returns an IamPolicyDocument, or None if the policy doesn't exist.
    """
    iam = session.client("iam")

    try:
        resp = iam.get_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
        )
    except iam.exceptions.NoSuchEntityException:
        return None

    return IamPolicyDocument.model_validate(resp["PolicyDocument"])


def statements_equal(a: IamPolicyStatement, b: IamPolicyStatement) -> bool:
    """
    Compare two statements logically, ignoring None fields and ordering noise.
    """
    da = a.model_dump(exclude_none=True)
    db = b.model_dump(exclude_none=True)
    return da == db


# ---------- UPDATED: upsert with diff ---------- #

def upsert_statement_for_role(
    session,
    role_name: str,
    policy_name: str,
    statement: IamPolicyStatement,
) -> IamPolicyDocument:
    """
    Create or update a single statement in an inline policy identified by Sid.

    - If the policy does not exist, it is created.
    - If a statement with the same Sid exists and is IDENTICAL, no AWS call is made.
    - If a statement with the same Sid exists but differs, it is replaced.
    - Otherwise, the statement is appended.

    Returns the final IamPolicyDocument.
    """
    policy_doc = get_inline_policy_for_role(session, role_name, policy_name)

    if policy_doc is None:
        # No policy yet → create new with this single statement
        policy_doc = IamPolicyDocument(Statement=[statement])
        put_inline_policy_for_role(session, role_name, policy_name, policy_doc)
        return policy_doc

    replaced = False
    changed = True

    for i, stmt in enumerate(policy_doc.Statement):
        if stmt.Sid == statement.Sid:
            replaced = True
            if statements_equal(stmt, statement):
                # No logical change → skip AWS update
                changed = False
            else:
                policy_doc.Statement[i] = statement
            break

    if not replaced:
        policy_doc.Statement.append(statement)

    if changed:
        put_inline_policy_for_role(session, role_name, policy_name, policy_doc)

    return policy_doc


def delete_statement_for_role(
    session,
    role_name: str,
    policy_name: str,
    sid: str,
) -> IamPolicyDocument | None:
    """
    Delete a single statement from an inline policy by Sid.
    - If the policy or statement does not exist, this is a no-op.
    - If, after deletion, there are no statements left, the inline policy
      itself is deleted from the role.
    Returns the updated IamPolicyDocument, or None if the policy was deleted.
    """
    iam = session.client("iam")
    policy_doc = get_inline_policy_for_role(session, role_name, policy_name)

    if policy_doc is None:
        return None

    new_statements = [s for s in policy_doc.Statement if s.Sid != sid]

    # Nothing removed
    if len(new_statements) == len(policy_doc.Statement):
        return policy_doc

    if not new_statements:
        iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
        return None

    policy_doc.Statement = new_statements
    put_inline_policy_for_role(session, role_name, policy_name, policy_doc)
    return policy_doc


from typing import Any, Literal, List
from pydantic import BaseModel, Field
import json


# ---------- TRUST POLICY MODELS ---------- #

class IamTrustPolicyStatement(BaseModel):
    Sid: str | None = None
    Effect: Literal["Allow", "Deny"] = "Allow"
    Principal: dict[str, Any]
    Action: List[str] | str
    Condition: dict[str, Any] | None = None


class IamTrustPolicyDocument(BaseModel):
    Version: str = "2012-10-17"
    Statement: List[IamTrustPolicyStatement] = Field(default_factory=list)


# ---------- BASIC READ / WRITE HELPERS ---------- #

def get_trust_policy_for_role(
    session,
    role_name: str,
) -> IamTrustPolicyDocument:
    """
    Fetch the trust (assume role) policy for a role.
    Raises if the role does not exist.
    """
    iam = session.client("iam")
    resp = iam.get_role(RoleName=role_name)

    # Boto3 returns AssumeRolePolicyDocument as a *dict*, not URL-encoded
    raw_doc = resp["Role"]["AssumeRolePolicyDocument"]
    return IamTrustPolicyDocument.model_validate(raw_doc)


def put_trust_policy_for_role(
    session,
    role_name: str,
    policy_doc: IamTrustPolicyDocument,
) -> None:
    """
    Replace the trust policy for a role with the given document.
    """
    iam = session.client("iam")

    iam.update_assume_role_policy(
        RoleName=role_name,
        PolicyDocument=policy_doc.model_dump_json(exclude_none=True),
    )


# ---------- DIFF / UPSERT / DELETE BY Sid ---------- #

def trust_statements_equal(
    a: IamTrustPolicyStatement,
    b: IamTrustPolicyStatement,
) -> bool:
    """
    Compare two trust policy statements logically, ignoring None fields.
    """
    da = a.model_dump(exclude_none=True)
    db = b.model_dump(exclude_none=True)
    return da == db


def upsert_trust_statement_for_role(
    session,
    role_name: str,
    statement: IamTrustPolicyStatement,
) -> IamTrustPolicyDocument:
    """
    Upsert a single trust policy statement identified by Sid.

    - If no existing statement with the same Sid: append.
    - If one exists and is identical: no AWS call (no-op).
    - If one exists and differs: replace and update policy.

    Returns the final IamTrustPolicyDocument.
    """
    # Require Sid for clean matching (great for GraphIaC Sids like "GraphIaCTrust:<g_id>")
    if statement.Sid is None:
        raise ValueError("IamTrustPolicyStatement.Sid must be set for upsert")

    policy_doc = get_trust_policy_for_role(session, role_name)

    replaced = False
    changed = True

    for i, stmt in enumerate(policy_doc.Statement):
        if stmt.Sid == statement.Sid:
            replaced = True
            if trust_statements_equal(stmt, statement):
                changed = False  # no logical change
            else:
                policy_doc.Statement[i] = statement
            break

    if not replaced:
        policy_doc.Statement.append(statement)

    if changed:
        put_trust_policy_for_role(session, role_name, policy_doc)

    return policy_doc


def delete_trust_statement_for_role(
    session,
    role_name: str,
    sid: str,
) -> IamTrustPolicyDocument:
    """
    Delete a single trust policy statement by Sid.
    If no such Sid exists, this is a no-op.
    Returns the updated IamTrustPolicyDocument.
    """
    policy_doc = get_trust_policy_for_role(session, role_name)

    new_statements = [s for s in policy_doc.Statement if s.Sid != sid]

    # No change
    if len(new_statements) == len(policy_doc.Statement):
        return policy_doc

    policy_doc.Statement = new_statements
    put_trust_policy_for_role(session, role_name, policy_doc)
    return policy_doc
