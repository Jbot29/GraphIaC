from typing import Optional, Literal, Dict

from .iam_role import IAMRolePolicyEdge,IAMRoleInlinePolicyEdge

from .iam_policy import IamPolicyDocument,IamPolicyStatement,get_inline_policy_for_role,put_inline_policy_for_role
from GraphIaC.models import BaseNode,BaseEdge
"""


"""

class LambdaDynamoEdge(IAMRoleInlinePolicyEdge):
    role_g_id: str 
    lambda_node_g_id: str
    dynamo_node_g_id: str  
    policy_doc: Optional[IamPolicyDocument] = None

    @property
    def source_g_id(self) -> str:
        return self.lambda_node_g_id
    
    @property
    def destination_g_id(self) -> str:
        return self.dynamo_node_g_id
    
    def read(self,session,G):
        role_name = G.nodes[self.role_g_id]['data'].read_id

        p = get_inline_policy_for_role(session,role_name,self.policy_name)

        if not p:
            return None
        
        return LambdaDynamoEdge(role_g_id=self.role_g_id,lambda_node_g_id=self.lambda_node_g_id,dynamo_node_g_id=self.dynamo_node_g_id,policy_doc=p)


    def create(self,session,G):
        """Create a new inline policy for this edge """
        role_name = G.nodes[self.role_g_id]['data'].read_id

        table_arn = G.nodes[self.dynamo_node_g_id]['data'].read_arn(session)

        statement = IamPolicyStatement(
            Sid="FullDynamoAccess",
            Effect="Allow",
            Action="dynamodb:*",  # full access to dynamodb actions
            Resource=[
                f"{table_arn}",
                f"{table_arn}/index/*"
            ]
        )
        self.policy_doc = IamPolicyDocument(Statement=[statement])
        

        
        result = put_inline_policy_for_role(session,role_name,self.policy_name,self.policy_doc)
        return True
        

    def update(self,session,G):
        pass
    def delete(self,session,G):
        pass
