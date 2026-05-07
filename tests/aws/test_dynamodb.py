import pytest

import GraphIaC
from GraphIaC.aws.dynamodb import DynamoKey, DynamoTable
from GraphIaC.main import OperationType

REGION = "us-east-2"


@pytest.fixture
def cleanup_tables(aws_session):
    """Guarantee table deletion even if a test fails midway."""
    created = []
    yield created
    ddb = aws_session.client("dynamodb", region_name=REGION)
    for name in created:
        try:
            ddb.delete_table(TableName=name)
            ddb.get_waiter("table_not_exists").wait(TableName=name)
        except ddb.exceptions.ResourceNotFoundException:
            pass


@pytest.mark.aws
def test_dynamodb_full_lifecycle(aws_session, db_conn, unique_suffix, cleanup_tables):
    table_name = f"graphiac-test-{unique_suffix}"
    cleanup_tables.append(table_name)

    p_key = DynamoKey(name="pk", attr_type="S")
    table = DynamoTable(
        g_id="test_dynamo_table",
        table_name=table_name,
        partition_key=p_key,
        region=REGION,
    )

    # Plan before creation: expect CREATE
    state = GraphIaC.init(aws_session, db_conn)
    GraphIaC.add_node(state, table)
    ops = GraphIaC.plan(state)
    assert OperationType.CREATE in [op.operation for op in ops]

    # Run: creates the table in AWS and records it in the DB
    state = GraphIaC.init(aws_session, db_conn)
    GraphIaC.add_node(state, table)
    GraphIaC.run(state)

    # Verify via direct boto3 — independent of GraphIaC
    ddb = aws_session.client("dynamodb", region_name=REGION)
    resp = ddb.describe_table(TableName=table_name)
    t = resp["Table"]
    assert t["TableStatus"] == "ACTIVE"
    key_names = {k["AttributeName"] for k in t["KeySchema"]}
    assert "pk" in key_names

    # Re-plan with same config: no CREATE expected (table already exists and is recorded)
    state = GraphIaC.init(aws_session, db_conn)
    GraphIaC.add_node(state, table)
    ops = GraphIaC.plan(state)
    assert OperationType.CREATE not in [op.operation for op in ops]

    # Delete: run with no nodes — DB row is orphaned, triggers DELETE
    state = GraphIaC.init(aws_session, db_conn)
    GraphIaC.run(state)

    # Verify deletion via direct boto3
    with pytest.raises(ddb.exceptions.ResourceNotFoundException):
        ddb.describe_table(TableName=table_name)


@pytest.mark.aws
def test_dynamodb_with_sort_key(aws_session, db_conn, unique_suffix, cleanup_tables):
    table_name = f"graphiac-test-sk-{unique_suffix}"
    cleanup_tables.append(table_name)

    table = DynamoTable(
        g_id="test_dynamo_table_sk",
        table_name=table_name,
        partition_key=DynamoKey(name="pk", attr_type="S"),
        sort_key=DynamoKey(name="sk", attr_type="N"),
        region=REGION,
    )

    state = GraphIaC.init(aws_session, db_conn)
    GraphIaC.add_node(state, table)
    GraphIaC.run(state)

    ddb = aws_session.client("dynamodb", region_name=REGION)
    resp = ddb.describe_table(TableName=table_name)
    key_schema = {k["AttributeName"]: k["KeyType"] for k in resp["Table"]["KeySchema"]}
    assert key_schema["pk"] == "HASH"
    assert key_schema["sk"] == "RANGE"

    attr_types = {a["AttributeName"]: a["AttributeType"] for a in resp["Table"]["AttributeDefinitions"]}
    assert attr_types["sk"] == "N"

    # Cleanup
    state = GraphIaC.init(aws_session, db_conn)
    GraphIaC.run(state)

    with pytest.raises(ddb.exceptions.ResourceNotFoundException):
        ddb.describe_table(TableName=table_name)
