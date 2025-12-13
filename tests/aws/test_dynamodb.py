
import os
import boto3


import sqlite3

import GraphIaC
from GraphIaC.aws.dynamodb import DynamoTable,DynamoKey


TEST_DB_PATH = "dynamodb_test.db"
TEST_PROFILE = "graphiac_profile"


p_key = DynamoKey(name="dkey",attr_type="S")
table = DynamoTable(g_id="test_dynamo_table",table_name="test_table",partition_key=p_key)

#test model

"""
def test_model():
    p_key = DynamoKey(name="dkey",attr_type="S")
    table = DynamoTable(g_id="test_dynamo_table",table_name="test_table",partition_key=p_key)



# Test CRUD


def test_add():

    db_conn = sqlite3.connect(TEST_DB_PATH)
    
    session = boto3.session.Session(profile_name=TEST_PROFILE)


    gioc = GraphIaC.init(session,db_conn)

    GraphIaC.add_node(gioc,table)
    
    changes = GraphIaC.plan(gioc)


    gioc = GraphIaC.init(session,db_conn)

    GraphIaC.add_node(gioc,table)
    
    changes = GraphIaC.run(gioc)


def test_read():
    
    db_conn = sqlite3.connect(TEST_DB_PATH)
    
    session = boto3.session.Session(profile_name=TEST_PROFILE)


    gioc = GraphIaC.init(session,db_conn)

    GraphIaC.add_node(gioc,table)
    changes = GraphIaC.plan(gioc)
    print(changes)
"""
def test_delete():
    
    db_conn = sqlite3.connect(TEST_DB_PATH)
    
    session = boto3.session.Session(profile_name=TEST_PROFILE)


    gioc = GraphIaC.init(session,db_conn)

    changes = GraphIaC.plan(gioc)
    print("DELETE")
    print(changes)

    print("DELETE RUN")
    gioc = GraphIaC.init(session,db_conn)

    changes = GraphIaC.run(gioc)
    
    

