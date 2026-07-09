import pytest

import GraphIaC
from GraphIaC.aws.cognito import CognitoPoolClientEdge, CognitoUserPool, CognitoUserPoolClient
from GraphIaC.main import OperationType

REGION = "us-east-2"


@pytest.fixture
def cleanup_pools(aws_session):
    """Guarantee pool deletion even if a test fails midway."""
    created = []
    yield created
    idp = aws_session.client("cognito-idp", region_name=REGION)
    pools = idp.list_user_pools(MaxResults=60)["UserPools"]
    for name in created:
        for p in pools:
            if p["Name"] == name:
                try:
                    idp.update_user_pool(UserPoolId=p["Id"], DeletionProtection="INACTIVE")
                    idp.delete_user_pool(UserPoolId=p["Id"])
                except idp.exceptions.ResourceNotFoundException:
                    pass


@pytest.mark.aws
def test_cognito_full_lifecycle(aws_session, db_conn, unique_suffix, cleanup_pools):
    pool_name = f"graphiac-test-{unique_suffix}"
    cleanup_pools.append(pool_name)

    def build(state):
        pool = CognitoUserPool(g_id="pool", pool_name=pool_name, region=REGION)
        client = CognitoUserPoolClient(
            g_id="client",
            client_name=f"{pool_name}-ui",
            callback_urls=["https://example.com/callback"],
        )
        GraphIaC.add_node(state, pool)
        GraphIaC.add_node(state, client)
        GraphIaC.add_edge(state, CognitoPoolClientEdge(pool_g_id="pool", client_g_id="client"))
        return pool, client

    # Plan before creation: expect CREATE + edge
    state = GraphIaC.init(aws_session, db_conn)
    build(state)
    ops = [op.operation for op in GraphIaC.plan(state)]
    assert OperationType.CREATE in ops
    assert OperationType.CREATE_EDGE in ops

    # Run: creates the pool and app client
    state = GraphIaC.init(aws_session, db_conn)
    build(state)
    GraphIaC.run(state)

    # Verify via direct boto3 — independent of GraphIaC
    idp = aws_session.client("cognito-idp", region_name=REGION)
    pools = [p for p in idp.list_user_pools(MaxResults=60)["UserPools"] if p["Name"] == pool_name]
    assert len(pools) == 1
    pool_desc = idp.describe_user_pool(UserPoolId=pools[0]["Id"])["UserPool"]
    assert pool_desc["AdminCreateUserConfig"]["AllowAdminCreateUserOnly"] is True
    clients = idp.list_user_pool_clients(UserPoolId=pools[0]["Id"], MaxResults=60)[
        "UserPoolClients"
    ]
    assert any(c["ClientName"] == f"{pool_name}-ui" for c in clients)

    # Re-plan with same config: converged
    state = GraphIaC.init(aws_session, db_conn)
    build(state)
    assert GraphIaC.plan(state) == []

    # verify() passes on the locked-down defaults
    state = GraphIaC.init(aws_session, db_conn)
    build(state)
    assert GraphIaC.verify(state) == 0
