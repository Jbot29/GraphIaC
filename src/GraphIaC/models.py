from typing import ClassVar, Optional

from deepdiff import DeepDiff
from pydantic import BaseModel


class VerifyResult(BaseModel):
    name: str
    passed: bool
    message: str = ""


class BaseNode(BaseModel):
    g_id: str

    # The IAM actions the DEPLOY identity needs so this class's
    # read/create/update/delete/verify can run — introspected by
    # deploy_policy.py to generate minimal deploy policies (the same
    # pattern as the DSL registry). Declare on every concrete class.
    deploy_actions: ClassVar[list] = []

    @property
    def read_id(self) -> Optional[str]:
        return None

    def ready(self) -> bool:
        """Is this resource usable by things that depend on it?

        The DSL planner gates attribute references (other.field) on the
        referenced node's live state being ready(); until then dependents
        are BLOCKED. Override for resources with a not-yet-usable phase
        (e.g. an ACM certificate that exists but is not ISSUED).
        """
        return True

    @classmethod
    def read(self, session, G, g_id, read_id):
        pass

    def create(self, session, G) -> bool:
        pass

    def update(self, session, G):
        pass

    def delete(self, session, G):
        pass

    def verify(self, session, G) -> list:
        return []

    def diff(self, session, G, diff_object):
        if not isinstance(diff_object, self.__class__):
            return False

        # Only compare fields self has explicitly set (non-None, non-g_id).
        # This prevents sparse infra.py definitions (zone_id=None, arn=None, etc.)
        # from always diffing against fully-populated AWS state.
        self_d = {k: v for k, v in self.model_dump().items() if k != "g_id" and v is not None}
        other_d = {k: diff_object.model_dump().get(k) for k in self_d}
        return DeepDiff(self_d, other_d)

    def import_from_provider(self):
        class_name = self.__class__.__name__
        # Build a comma-separated list of key=value pairs using repr(value)
        fields_str = ", ".join(f"{k}={repr(v)}" for k, v in self.dict().items())
        # Construct something like: MyModel(field1='abc', field2=123)
        return f"{class_name}({fields_str})"


class BaseEdge(BaseModel):
    # g_id: str
    # source_g_id: str
    # destination_g_id: str
    # node_1_g_id: str
    # node_2_g_id: str

    # An edge may declare that its DESTINATION cannot be provisioned until its
    # SOURCE's live state is ready() — e.g. a CloudFront distribution needs its
    # ACM certificate ISSUED before it can even be created. The DSL planner
    # then marks the destination (and everything touching it) BLOCKED instead
    # of letting create() fail against AWS. Data-shaped dependencies use
    # attribute references instead; this flag is for relationship-shaped ones.
    gates_destination: ClassVar[bool] = False

    # See BaseNode.deploy_actions.
    deploy_actions: ClassVar[list] = []

    @property
    def source_g_id(self):
        return None

    @property
    def destination_g_id(self):
        return None

    def read(self, session):
        pass

    def create(self, session, G) -> bool:
        pass

    def update(self, session, G):
        pass

    def delete(self, session, G):
        pass

    def verify(self, session, G) -> list:
        return []

    def diff(self, session, G, diff_object):
        if not isinstance(diff_object, self.__class__):
            return False

        self_d = {k: v for k, v in self.model_dump().items() if v is not None}
        other_d = {k: diff_object.model_dump().get(k) for k in self_d}
        return DeepDiff(self_d, other_d)
