from typing import ClassVar, Dict, Literal, Optional

from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

from GraphIaC.models import BaseNode

from ..logs import setup_logger

logger = setup_logger()


BillingMode = Literal["PAY_PER_REQUEST", "PROVISIONED"]
AttrType = Literal["S", "N", "B"]


class DynamoKey(BaseModel):
    name: str
    attr_type: AttrType = Field("S", description="DynamoDB attribute type: S, N, or B")


class DynamoTable(BaseNode):
    deploy_actions: ClassVar[list] = [
        "dynamodb:CreateTable",
        "dynamodb:DescribeTable",
        "dynamodb:UpdateTable",
        "dynamodb:DeleteTable",
        "dynamodb:ListTagsOfResource",
        "dynamodb:TagResource",
    ]

    table_name: str = Field(..., alias="table_name")
    region: str = "us-east-2"
    partition_key: DynamoKey
    sort_key: Optional[DynamoKey] = None

    billing_mode: BillingMode = "PAY_PER_REQUEST"
    read_capacity: Optional[int] = Field(0, description="Only used when billing_mode=PROVISIONED")
    write_capacity: Optional[int] = Field(0, description="Only used when billing_mode=PROVISIONED")

    tags: Dict[str, str] = Field(default_factory=dict)

    @property
    def read_id(self) -> Optional[str]:
        return self.table_name

    def read_arn(self, session):
        dynamodb = session.client("dynamodb", region_name=self.region)

        resp = dynamodb.describe_table(TableName=self.table_name)
        return resp["Table"]["TableArn"]

    @classmethod
    def read(self, session, G, g_id, read_id, region="us-east-2"):
        logger.info(f"{self.__class__.__name__}: Exists {self}")
        dynamodb = session.client("dynamodb", region_name=region)

        try:
            resp = dynamodb.describe_table(TableName=read_id)
        except dynamodb.exceptions.ResourceNotFoundException:
            return None

        # Extract keys
        key_schema = resp["Table"]["KeySchema"]
        attr_defs = {
            a["AttributeName"]: a["AttributeType"] for a in resp["Table"]["AttributeDefinitions"]
        }

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

        billing_mode = resp["Table"].get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
        throughput = resp["Table"].get("ProvisionedThroughput", {})

        # Tags need a separate call
        tags_resp = dynamodb.list_tags_of_resource(ResourceArn=resp["Table"]["TableArn"])
        tags = {t["Key"]: t["Value"] for t in tags_resp.get("Tags", [])}

        return DynamoTable(
            g_id=g_id,
            table_name=read_id,
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

    def create(self, session, G):
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
            waiter = dynamodb.get_waiter("table_exists")
            waiter.wait(TableName=self.table_name)
            return resp
        except ClientError:
            raise

    def delete(self, session, G):
        dynamodb = session.client("dynamodb", region_name=self.region)

        try:
            resp = dynamodb.delete_table(TableName=self.table_name)
            waiter = dynamodb.get_waiter("table_not_exists")
            waiter.wait(TableName=self.table_name)
            return resp
        except ClientError:
            raise
