import time
from typing import List

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel


class CertificateHostedZoneEdge(BaseModel):
    hz_g_id: str
    cert_g_id: str
    changes: List[str]

    @classmethod
    def generate(cls,hosted_zone,cert):
        
        return CertificateHostedZoneEdge(hz_g_id=hosted_zone.g_id,cert_g_id=cert.g_id,changes=[])

    
class Certificate(BaseModel):
    g_id: str    
    domain_name: str
    hosted_zone_id: str
    #arn: Optional[str] = None
    arn: str

    def exists(self,session):
        print(f"{self.__class__.__name__}: Exists {self}")

        if self.arn and check_certificate_exists_by_arn(session,self.arn):
            return True

        return False

    def create(self,session,G):
        print(f"{self.__class__.__name__}: Create {self}")
        hosted_zone = None
        
        for e in G.neighbors(self.g_id):
            
            print(f"\tEdge: {e}")
            edge = G.edges[self.g_id,e]
            edge_node = G.nodes[e]['data']
            print(f"\t{edge} {edge_node} {edge_node.__class__.__name__}")
                
            if edge_node.__class__.__name__ == 'HostedZone':
                hosted_zone = edge_node
                

        #create cert
        print(f"Create cert for  Zone: {hosted_zone}")
        #request_certificate(session,hosted_zone.domain_name, hosted_zone.zone_id)
        add_dns_validation(session,self.arn, hosted_zone.domain_name, hosted_zone.zone_id)


def check_certificate_exists_by_arn(session,certificate_arn):
    # Initialize the ACM client
    acm = session.client('acm', region_name='us-east-1')  # Ensure this is the correct region

    try:
        # Describe the certificate by ARN
        response = acm.describe_certificate(CertificateArn=certificate_arn)
        
        # If the call is successful, the certificate exists
        print(f"Certificate found: {response['Certificate']}")
        return True
    
    except ClientError as e:
        # If the certificate does not exist, catch the exception
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            print(f"Certificate with ARN {certificate_arn} does not exist.")
        else:
            print(f"An error occurred: {e}")
        return False
    
def request_certificate(session,domain_name, hosted_zone_id):
    # Initialize the ACM client for certificate requests
    print(f"Create cert for  Zone: {domain_name} {hosted_zone_id}")
    
    acm = session.client('acm', region_name='us-east-1')  # Certificates for CloudFront must be in us-east-1

    try:
        # Request the certificate with DNS validation
        response = acm.request_certificate(
            DomainName=domain_name,
            ValidationMethod='DNS',
            SubjectAlternativeNames=[
                f"www.{domain_name}"
            ],
            Tags=[
                {
                    'Key': 'Name',
                    'Value': f'{domain_name} Certificate'
                }
            ]
        )
        certificate_arn = response['CertificateArn']
        print(f"Certificate requested for {domain_name}. ARN: {certificate_arn}")
        return certificate_arn

    except ClientError as e:
        print(f"Failed to request certificate: {e}")
        return None

def get_dns_validation(session,certificate_arn, domain_name,hosted_zone_id):
    edge_changes = []    
    acm = session.client('acm', region_name='us-east-1')    
    try:
        # Get the DNS validation records from ACM
        response = acm.describe_certificate(CertificateArn=certificate_arn)
        validation_options = response['Certificate']['DomainValidationOptions']


        for option in validation_options:
            dns_record = option['ResourceRecord']


            change_batch = {'Changes': [
                            {
                                'Action': 'UPSERT',
                                'ResourceRecordSet': {
                                    'Name': dns_record['Name'],
                                    'Type': dns_record['Type'],
                                    'TTL': 300,
                                    'ResourceRecords': [{'Value': dns_record['Value']}]
                                }
                            }
                        ]
                    }
                
            edge_changes.append(change_batch)
    except ClientError as e:
        print(f"Failed to get DNS validation record: {e}")
    return edge_changes

def is_cert_valid(session,certificate_arn):
    acm = session.client('acm', region_name='us-east-1')    
    response = acm.describe_certificate(CertificateArn=certificate_arn)
    for option in validation_options:
            if option['ValidationStatus'] == 'SUCCESS':
                return True

    return False
                
    
    
def add_dns_validation(session,certificate_arn, domain_name,hosted_zone_id):
    # Initialize ACM and Route 53 clients
    acm = session.client('acm', region_name='us-east-1')
    route53 = session.client('route53')
    print(f"Create dns validation for  Zone: {certificate_arn} {domain_name} {hosted_zone_id}")

    try:
        # Get the DNS validation records from ACM
        response = acm.describe_certificate(CertificateArn=certificate_arn)
        validation_options = response['Certificate']['DomainValidationOptions']

        edge_changes = []
        # Loop through each validation option and add a record to Route 53
        for option in validation_options:
            if option['ValidationStatus'] != 'SUCCESS':
                dns_record = option['ResourceRecord']

                # Add DNS record to Route 53
                change_batch = {'Changes': [
                            {
                                'Action': 'UPSERT',
                                'ResourceRecordSet': {
                                    'Name': dns_record['Name'],
                                    'Type': dns_record['Type'],
                                    'TTL': 300,
                                    'ResourceRecords': [{'Value': dns_record['Value']}]
                                }
                            }
                        ]
                    }
                
                route53.change_resource_record_sets(
                    HostedZoneId=hosted_zone_id,
                    ChangeBatch=change_batch
                        
                )

                edge_changes.append(change_batch)
                print(f"Added DNS validation record for {domain_name}: {dns_record}")

    except ClientError as e:
        print(f"Failed to add DNS validation record: {e}")

def check_certificate_status(certificate_arn):
    # Initialize the ACM client
    acm = boto3.client('acm', region_name='us-east-1')

    # Check the certificate status in a loop
    while True:
        response = acm.describe_certificate(CertificateArn=certificate_arn)
        status = response['Certificate']['Status']
        print(f"Certificate status: {status}")

        if status == 'ISSUED':
            print("Certificate has been successfully issued.")
            break
        elif status == 'FAILED':
            print("Certificate issuance failed.")
            break
        time.sleep(30)  # Wait and recheck status every 30 seconds

# Usage

"""
domain_name = 'example.com'  # Replace with your domain
hosted_zone_id = 'Z3P5QSUBK4POTI'  # Replace with your hosted zone ID

certificate_arn = request_certificate(domain_name, hosted_zone_id)

if certificate_arn:
    add_dns_validation(certificate_arn, hosted_zone_id, domain_name)
    check_certificate_status(certificate_arn)
"""
