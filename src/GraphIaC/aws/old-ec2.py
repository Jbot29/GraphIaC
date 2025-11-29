#Graph IOC






"""

class ECSCluster(BaseModel):
    id: str
    desc: str

class ECSTaskDefinition(BaseModel):
    id: str
    desc: str
    
class ECSService(BaseModel):
    id: str
    desc: str

    














def create_ecs_cluster(s):


    response = ecs.create_cluster(
        clusterName=s.id
    )

    print(response['cluster']['clusterArn'])



def create_ecs_service(cluster,service,taskdef,targetgrp,subnets,sg,container_name):
    response = ecs.create_service(
        cluster=cluster.id,
        serviceName=service.id,
        taskDefinition=taskdef.id,
        loadBalancers=[
            {
                'targetGroupArn': targetgrp.arn,
                'containerName': container_name,
                'containerPort': 80
            }
        ],
        desiredCount=2,  # Number of tasks
        launchType='FARGATE',
        networkConfiguration={
            'awsvpcConfiguration': {
                'subnets': subnets,
                'securityGroups': [
                    sg.aws_id
                ],
                'assignPublicIp': 'ENABLED'
            }
        }
    )

    print(f"Service ARN: {response['service']['serviceArn']}")    
"""
