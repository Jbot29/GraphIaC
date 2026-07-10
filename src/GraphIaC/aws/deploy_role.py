from typing import ClassVar, Optional

from botocore.exceptions import ClientError

from GraphIaC.models import VerifyResult

from ..logs import setup_logger
from .iam_policy import IamTrustPolicyDocument, IamTrustPolicyStatement
from .iam_role import IAMRole, role_create, role_read

logger = setup_logger()

POLICY_NAME = "GraphIaCDeployPolicy"


def _generated_policy():
    # lazy import: deploy_policy imports model_map, which imports this module
    from GraphIaC.deploy_policy import policy_for_all

    return policy_for_all()


def _account_id(session):
    return session.client("sts").get_caller_identity()["Account"]


class DeployRole(IAMRole):
    """The identity that deploys your infrastructure — GraphIaC managing its
    own deployer (see examples/get-started/).

    A self-describing node: its inline policy is generated from every
    registered node/edge class's deploy_actions, so `run` (re)syncs the role
    as GraphIaC grows, and `verify` reports any missing actions. Trusts the
    account root, so any principal in your account that is allowed
    sts:AssumeRole can assume it — attach that one permission to your human
    IAM user and use an assume-role profile day to day.
    """

    deploy_actions: ClassVar[list] = [
        # bootstrapping the bootstrapper — run setup with an admin-ish profile
        "iam:CreateRole",
        "iam:GetRole",
        "iam:PutRolePolicy",
        "iam:GetRolePolicy",
        "sts:GetCallerIdentity",
    ]

    name: str = "graphiac-deploy"
    account_id: Optional[str] = None

    def _trust(self, session):
        acct = self.account_id or _account_id(session)
        return IamTrustPolicyDocument(
            Statement=[
                IamTrustPolicyStatement(
                    Sid="GraphIaCTrustAccount",
                    Effect="Allow",
                    Principal={"AWS": f"arn:aws:iam::{acct}:root"},
                    Action="sts:AssumeRole",
                )
            ]
        )

    def _live_policy_actions(self, session):
        iam = session.client("iam")
        try:
            doc = iam.get_role_policy(RoleName=self.name, PolicyName=POLICY_NAME)[
                "PolicyDocument"
            ]
        except ClientError as e:
            if e.response["Error"]["Code"] == "NoSuchEntity":
                return None
            raise
        actions = set()
        for stmt in doc.get("Statement", []):
            acts = stmt.get("Action", [])
            actions.update([acts] if isinstance(acts, str) else acts)
        return actions

    def _put_policy(self, session):
        import json

        iam = session.client("iam")
        iam.put_role_policy(
            RoleName=self.name,
            PolicyName=POLICY_NAME,
            PolicyDocument=json.dumps(_generated_policy()),
        )

    def create(self, session, G):
        self.account_id = _account_id(session)
        self.arn = role_create(session, self.name, self._trust(session))
        if not self.arn:
            return False
        self._put_policy(session)
        logger.info(f"Created deploy role {self.name} with the full GraphIaC policy")
        logger.info(
            "Add this profile to ~/.aws/config and use it for every GraphIaC command:\n"
            f"    [profile graphiac]\n"
            f"    role_arn = {self.arn}\n"
            f"    source_profile = <the profile you just ran this with>\n"
            f"    region = us-east-2"
        )
        return True

    def read(self, session, G, g_id, read_id):
        role_arn, _ = role_read(session, self.name)
        if not role_arn:
            return None
        return DeployRole(g_id=self.g_id, name=self.name, arn=role_arn)

    def update(self, session, G):
        # re-sync the policy — this is how the role learns new services as
        # GraphIaC grows: pip upgrade, run setup again
        self._put_policy(session)
        logger.info(f"Re-synced {POLICY_NAME} on {self.name}")
        return True

    def diff(self, session, G, diff_object):
        if not isinstance(diff_object, IAMRole):
            return False
        live = self._live_policy_actions(session)
        wanted = set()
        for stmt in _generated_policy()["Statement"]:
            wanted.update(stmt["Action"])
        if live is None or not wanted <= live:
            missing = sorted(wanted - (live or set()))
            return {"missing_actions": missing[:5] + (["..."] if len(missing) > 5 else [])}
        return False

    def verify(self, session, G) -> list:
        role_arn, _ = role_read(session, self.name)
        if not role_arn:
            return [VerifyResult(name="Deploy role exists", passed=False,
                                 message=f"no role named {self.name}")]
        live = self._live_policy_actions(session) or set()
        wanted = set()
        for stmt in _generated_policy()["Statement"]:
            wanted.update(stmt["Action"])
        missing = sorted(wanted - live)
        return [
            VerifyResult(name="Deploy role exists", passed=True, message=role_arn),
            VerifyResult(
                name="Policy covers every registered type",
                passed=not missing,
                message="up to date" if not missing
                        else f"missing {len(missing)} actions (run setup again): {', '.join(missing[:5])}",
            ),
        ]
