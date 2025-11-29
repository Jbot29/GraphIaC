import boto3

from pydantic import BaseModel
from typing import Optional,List
from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode


class TargetGroup(BaseNode):
    name: str
    vpc_id: str
    target_type: str
    arn: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.name
    
    @classmethod
    def read(self,session,G,g_id,read_id):
        tg = read_target_group(session,read_id)

        protocol = tg.get("Protocol", "UNKNOWN")
        port = tg.get("Port", 0)
        desc_str = f"{protocol} on port {port}"

        # Build the TargetGroup model
        return TargetGroup(
            g_id=g_id,
            name=tg["TargetGroupName"],
            desc=desc_str,
            vpc_id=tg["VpcId"],
            target_type=tg["TargetType"],
            arn=tg["TargetGroupArn"]
        )
        
        #return tg


def create_target_group(tg):
    # Step 3: Create a Target Group
    response = elb.create_target_group(
        Name=tg.id,
        Protocol='HTTP',
        Port=80,
        VpcId=tg.vpc_id,
        HealthCheckProtocol='HTTP',
        HealthCheckPort='80',
        HealthCheckPath='/',
        TargetType=tg.target_type
    )

    target_group_arn = response['TargetGroups'][0]['TargetGroupArn']
    print(f"Target Group ARN: {target_group_arn}")

#docbot_alb_tg = TargetGroup(id="docbot-alb-tg",desc="Docbot Target Group",vpc_id="vpc-9f14cbfb",target_type="ip")


def read_target_group(session,tg_identifier, region_name = "us-east-1"):
    
    elbv2 = session.client("elbv2", region_name=region_name)

    # Decide whether it's an ARN or a Name
    if tg_identifier.startswith("arn:aws:"):
        describe_args = {"TargetGroupArns": [tg_identifier]}
    else:
        describe_args = {"Names": [tg_identifier]}

    try:
        response = elbv2.describe_target_groups(**describe_args)
    except ClientError as e:
        raise RuntimeError(f"Error describing Target Group '{tg_identifier}': {e}")

    tgs = response.get("TargetGroups", [])
    if not tgs:
        raise RuntimeError(f"No target group found matching '{tg_identifier}'")
    if len(tgs) > 1:
        raise RuntimeError(
            f"Multiple target groups returned for '{tg_identifier}', please be more specific."
        )

    # Exactly one TG
    tg = tgs[0]
    return tg
    # Fill in your Pydantic model fields:
    # We'll assume `id` = TargetGroupName.
    # For `desc`, we have no official TG description, so let's store something custom.
    # E.g., "Protocol + Port"
