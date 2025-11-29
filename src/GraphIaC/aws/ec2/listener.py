import boto3

from pydantic import BaseModel
from typing import Optional,List
from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode

class Listener(BaseNode):
    arn: str

    @property
    def read_id(self) -> Optional[str]:
        return self.arn

    @classmethod
    def read(self,session,G,g_id,read_id):
        print(session)
        l =  read_listener(session,g_id, read_id)

        return Listener(
            g_id=g_id,
            arn=read_id,             # We'll store the entire listener ARN here
        )
        

        
def create_listener(l):
    # Step 4: Create a Listener for the Load Balancer
    response = elb.create_listener(
        LoadBalancerArn=l.lb_arn,
        Protocol='HTTP',
        Port=80,
        DefaultActions=[
            {
                'Type': 'forward',
                'TargetGroupArn': l.tg_arn
            }
        ]
    )

    listener_arn = response['Listeners'][0]['ListenerArn']
    print(f"Listener ARN: {listener_arn}")



def read_listener(session,g_id, listener_arn,region_name = "us-east-1"):
    """
    Retrieves a single Listener by ARN and populates a Listener Pydantic model.
    
    :param listener_arn: ARN of the listener (e.g., 'arn:aws:elasticloadbalancing:...')
    :param region_name: AWS region.
    :return: A Listener object representing the AWS listener.
    :raises RuntimeError: If not found or multiple found (unusual).
    """
    elbv2 = session.client("elbv2", region_name=region_name)

    try:
        response = elbv2.describe_listeners(ListenerArns=[listener_arn])
    except ClientError as e:
        raise RuntimeError(f"Error describing listener '{listener_arn}': {e}")

    listeners = response.get("Listeners", [])
    if not listeners:
        raise RuntimeError(f"No listener found with ARN: {listener_arn}")
    if len(listeners) > 1:
        # This is theoretically unusual since an ARN should be unique
        raise RuntimeError(f"Multiple listeners returned for ARN '{listener_arn}'.")

    return  listeners[0]
    # We have exactly one listener
    l = listeners[0]

    # Build a 'desc' from protocol and port for convenience
    protocol = l.get("Protocol", "UNKNOWN")
    port = l.get("Port", -1)
    desc_str = f"{protocol}:{port}"

    # Extract the default action target group ARN if there's a forward action
    tg_arn = None
    default_actions = l.get("DefaultActions", [])
    if default_actions:
        # Typically there's at least one action
        # If the action type is 'forward', we can retrieve 'TargetGroupArn'
        if default_actions[0]["Type"] == "forward":
            tg_arn = default_actions[0].get("TargetGroupArn")




"""
# Add nodes
G.add_node("listener1", type="listener", desc="HTTP:80")
G.add_node("tg1", type="target_group", desc="Target group 1")
G.add_node("tg2", type="target_group", desc="Target group 2")

# A single listener with edges to multiple target groups
G.add_edge("listener1", "tg1")
G.add_edge("listener1", "tg2")

"""


def create_path_based_rule(
    listener_arn: str,
    path_pattern: str,
    target_group_arn: str,
    priority: int,
    region_name: str = "us-east-1"
) -> str:
    """
    Creates a path-based rule for the given listener ARN.
    E.g. path_pattern="/foo/*" -> forward to target_group_arn
    The 'priority' must be unique among rules on that listener.
    Returns the newly created rule ARN.
    """
    elbv2 = boto3.client("elbv2", region_name=region_name)

    response = elbv2.create_rule(
        ListenerArn=listener_arn,
        Priority=priority,  # Must be unique on this listener
        Conditions=[
            {
                "Field": "path-pattern",
                "Values": [path_pattern]
            }
        ],
        Actions=[
            {
                "Type": "forward",
                "TargetGroupArn": target_group_arn
            }
        ]
    )
    
    rule_arn = response["Rules"][0]["RuleArn"]
    print(f"Created rule for path '{path_pattern}' -> TG {target_group_arn} at priority {priority}")
    return rule_arn
