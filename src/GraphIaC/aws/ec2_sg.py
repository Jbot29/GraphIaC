import boto3

from pydantic import BaseModel
from typing import Optional,List
from botocore.exceptions import ClientError

from ..models import BaseNode
"""

ec2 = session.client('ec2',region_name='us-east-1')
elb = session.client('elbv2',region_name='us-east-1')
ecs = session.client('ecs',region_name='us-east-1')
"""

class SecurityGroup(BaseNode):
    sg_id: str
    desc: str
    vpc_id: str
    arn: Optional[str] = None

    @property
    def read_id(self) -> Optional[str]:
        return self.sg_id


    def read(self,session,G,g_id,read_id):
        status, sg_data = security_group_read(session,self.sg_id)
        return SecurityGroup(g_id=self.g_id,sg_id=sg_data['GroupId'],desc="",vpc_id="",arn=sg_data['SecurityGroupArn'])
        

    


def security_group_read(session,security_group_id):
    # Initialize a boto3 EC2 client
    ec2 = session.client('ec2',region_name='us-east-1')

    try:
        # Try to describe the security group by ID
        response = ec2.describe_security_groups(GroupIds=[security_group_id])
        # If successful, return True and details of the security group
        if response['SecurityGroups']:
            print(f"Security Group {security_group_id} exists.")
            return True, response['SecurityGroups'][0]
    except ClientError as e:
        # Catch the exception if the security group does not exist or another error occurs
        if 'InvalidGroup.NotFound' in str(e):
            print(f"Security Group {security_group_id} does not exist.")
        else:
            print(f"An error occurred: {e}")
    return False, None


def create_sg():
    response = ec2.create_security_group(
        GroupName=docbot_sg.id,
        Description=docbot_sg.desc,
        VpcId=docbot_sg.vpc_id
    )


    security_group_id = response['GroupId']


    print(security_group_id )


def sg_ingress(security_group_id):

    # Authorize inbound traffic (e.g., allow HTTP on port 80)
    response = ec2.authorize_security_group_ingress(
        GroupId=security_group_id,
        IpPermissions=[
            {
                'IpProtocol': 'tcp',
                'FromPort': 80,
                'ToPort': 80,
                'IpRanges': [{'CidrIp': '0.0.0.0/0'}]
            }
        ]
    )

    print(response)
