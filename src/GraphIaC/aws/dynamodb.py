

import boto3

from pydantic import BaseModel
from typing import Optional,List
from botocore.exceptions import ClientError

from GraphIaC.models import BaseNode


from typing import Optional, Literal, Dict
from pydantic import BaseModel, Field, validator
import boto3
from botocore.exceptions import ClientError


BillingMode = Literal["PAY_PER_REQUEST", "PROVISIONED"]
AttrType = Literal["S", "N", "B"]


class DynamoKey(BaseModel):
    """Represents a single key attribute (HASH or RANGE)."""
    name: str
    attr_type: AttrType = Field("S", description="DynamoDB attribute type: S, N, or B")

    

class DynamoTable(BaseNode):
    table_name: str = Field(..., alias="table_name")
    region: str = "us-east-2"
    partition_key: DynamoKey
    sort_key: Optional[DynamoKey] = None

    billing_mode: BillingMode = "PAY_PER_REQUEST"
    read_capacity: Optional[int] = Field(
        5, description="Only used when billing_mode=PROVISIONED"
    )
    write_capacity: Optional[int] = Field(
        5, description="Only used when billing_mode=PROVISIONED"
    )

    tags: Dict[str, str] = Field(default_factory=dict)

    @property
    def read_id(self) -> Optional[str]:
        return self.table_name

    @classmethod
    def read(self,session,G,g_id,read_id,region="us-east-2"):
        
        print(f"{self.__class__.__name__}: Exists {self}")
        dynamodb = session.client("dynamodb",region_name=region)
    
        try:
            resp = dynamodb.describe_table(TableName=read_id)
            return True
        except dynamodb.exceptions.ResourceNotFoundException:
            return False
        
        # Extract keys
        key_schema = resp["KeySchema"]
        attr_defs = {a["AttributeName"]: a["AttributeType"] for a in resp["AttributeDefinitions"]}

        hash_def = next(k for k in key_schema if k["KeyType"] == "HASH")
        range_def = next((k for k in key_schema if k["KeyType"] == "RANGE"), None)

        partition_key = DynamoKey(
            name=hash_def["AttributeName"],
            attr_type=attr_defs[hash_def["AttributeName"]],
        )

        sort_key = None
        if range_def:
            sort_key = DynamoKey(
                name=range_def["AttributeName"],
                attr_type=attr_defs[range_def["AttributeName"]],
            )

        billing_mode = resp.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
        throughput = resp.get("ProvisionedThroughput", {})

        # Tags need a separate call
        tags_resp = dynamodb.list_tags_of_resource(
            ResourceArn=resp["TableArn"]
        )
        tags = {t["Key"]: t["Value"] for t in tags_resp.get("Tags", [])}

        return DynamoTable(
            table_name=table_name,
            region=region,
            partition_key=partition_key,
            sort_key=sort_key,
            billing_mode=billing_mode,
            read_capacity=throughput.get("ReadCapacityUnits"),
            write_capacity=throughput.get("WriteCapacityUnits"),
            tags=tags,
        )



    def _to_attribute_definitions(self):
        attrs = [
            {
                "AttributeName": self.partition_key.name,
                "AttributeType": self.partition_key.attr_type,
            }
        ]
        if self.sort_key:
            attrs.append(
                {
                    "AttributeName": self.sort_key.name,
                    "AttributeType": self.sort_key.attr_type,
                }
            )
        return attrs

    def _to_key_schema(self):
        schema = [
            {
                "AttributeName": self.partition_key.name,
                "KeyType": "HASH",
            }
        ]
        if self.sort_key:
            schema.append(
                {
                    "AttributeName": self.sort_key.name,
                    "KeyType": "RANGE",
                }
            )
        return schema

    def _to_tags(self):
        if not self.tags:
            return None
        return [{"Key": k, "Value": v} for k, v in self.tags.items()]

    def create(self,session,G):
        dynamodb = session.client("dynamodb", region_name=self.region)
        
        params: Dict = {
            "TableName": self.table_name,
            "AttributeDefinitions": self._to_attribute_definitions(),
            "KeySchema": self._to_key_schema(),
            "BillingMode": self.billing_mode,
        }

        if self.billing_mode == "PROVISIONED":
            params["ProvisionedThroughput"] = {
                "ReadCapacityUnits": self.read_capacity,
                "WriteCapacityUnits": self.write_capacity,
            }

        tags = self._to_tags()
        if tags:
            params["Tags"] = tags

        try:
            resp = dynamodb.create_table(**params)
            if True:
                waiter = dynamodb.get_waiter("table_exists")
                waiter.wait(TableName=self.table_name)
            return resp
        except ClientError as e:
            # Up to you if you want to wrap this in your own error type
            raise
        
        return True

    def delete(self, session, G):

        dynamodb = session.client("dynamodb", region_name=self.region)        

        try:
            resp = dynamodb.delete_table(TableName=self.table_name)
            waiter = dynamodb.get_waiter("table_not_exists")
            waiter.wait(TableName=self.table_name)
            return resp
        except ClientError as e:
            raise

        return None
'''



class DynamoTable(BaseModel):
    """
    Infra-level representation of a DynamoDB table.
    This is a *resource node* in your graph, not a data-access layer.
    """
    name: str = Field(..., alias="table_name")
    region: str = "us-east-1"

    partition_key: DynamoKey
    sort_key: Optional[DynamoKey] = None

    billing_mode: BillingMode = "PAY_PER_REQUEST"
    read_capacity: Optional[int] = Field(
        5, description="Only used when billing_mode=PROVISIONED"
    )
    write_capacity: Optional[int] = Field(
        5, description="Only used when billing_mode=PROVISIONED"
    )

    tags: Dict[str, str] = Field(default_factory=dict)

    class Config:
        allow_population_by_field_name = True

    # ---------- internal helpers ----------

    def _client(self, client=None):
        if client is not None:
            return client
        return boto3.client("dynamodb", region_name=self.region)

    def _to_attribute_definitions(self):
        attrs = [
            {
                "AttributeName": self.partition_key.name,
                "AttributeType": self.partition_key.attr_type,
            }
        ]
        if self.sort_key:
            attrs.append(
                {
                    "AttributeName": self.sort_key.name,
                    "AttributeType": self.sort_key.attr_type,
                }
            )
        return attrs

    def _to_key_schema(self):
        schema = [
            {
                "AttributeName": self.partition_key.name,
                "KeyType": "HASH",
            }
        ]
        if self.sort_key:
            schema.append(
                {
                    "AttributeName": self.sort_key.name,
                    "KeyType": "RANGE",
                }
            )
        return schema

    def _to_tags(self):
        if not self.tags:
            return None
        return [{"Key": k, "Value": v} for k, v in self.tags.items()]

    @validator("read_capacity", "write_capacity", always=True)
    def _capacities_required_for_provisioned(cls, v, values, field):
        billing_mode = values.get("billing_mode")
        if billing_mode == "PROVISIONED" and v is None:
            raise ValueError(f"{field.name} is required when billing_mode=PROVISIONED")
        return v

    # ---------- CRUD for the *table resource* ----------

    def create(self, client=None, wait: bool = True) -> Dict:
        """
        Create the DynamoDB table in AWS.

        Returns the create_table response. Optionally waits until the table exists.
        """
        dynamodb = self._client(client)

        params: Dict = {
            "TableName": self.name,
            "AttributeDefinitions": self._to_attribute_definitions(),
            "KeySchema": self._to_key_schema(),
            "BillingMode": self.billing_mode,
        }

        if self.billing_mode == "PROVISIONED":
            params["ProvisionedThroughput"] = {
                "ReadCapacityUnits": self.read_capacity,
                "WriteCapacityUnits": self.write_capacity,
            }

        tags = self._to_tags()
        if tags:
            params["Tags"] = tags

        try:
            resp = dynamodb.create_table(**params)
            if wait:
                waiter = dynamodb.get_waiter("table_exists")
                waiter.wait(TableName=self.name)
            return resp
        except ClientError as e:
            # Up to you if you want to wrap this in your own error type
            raise

    def read(self, client=None) -> Dict:
        """
        Read the current table description from AWS (DescribeTable).

        This does *not* mutate the model by default; it just returns the AWS view.
        """
        dynamodb = self._client(client)
        try:
            resp = dynamodb.describe_table(TableName=self.name)
            return resp["Table"]
        except ClientError as e:
            raise

    def update(self, client=None, wait: bool = True) -> Dict:
        """
        Update mutable properties of the table (billing mode / throughput).

        The intended flow is:
            1. Load a DynamoTable model (from code or from AWS)
            2. Mutate billing_mode/read_capacity/write_capacity
            3. Call update() to push changes to AWS
        """
        dynamodb = self._client(client)

        params: Dict = {
            "TableName": self.name,
        }

        # Update billing mode / throughput
        if self.billing_mode == "PAY_PER_REQUEST":
            params["BillingMode"] = "PAY_PER_REQUEST"
            # When switching from PROVISIONED -> PAY_PER_REQUEST,
            # ProvisionedThroughput is ignored.
        else:
            params["BillingMode"] = "PROVISIONED"
            params["ProvisionedThroughput"] = {
                "ReadCapacityUnits": self.read_capacity,
                "WriteCapacityUnits": self.write_capacity,
            }

        try:
            resp = dynamodb.update_table(**params)
            if wait:
                waiter = dynamodb.get_waiter("table_exists")
                waiter.wait(TableName=self.name)
            return resp
        except ClientError as e:
            raise

    # ---------- convenience factory ----------



'''
