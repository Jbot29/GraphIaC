from typing import NewType

from pydantic import constr

# Define a custom type for the role name
AwsName = NewType("RoleName", constr(pattern=r"^[A-Za-z0-9+=,.@_-]+$"))
