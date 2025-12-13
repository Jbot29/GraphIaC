
import networkx as nx
import boto3
from enum import Enum
from pydantic import BaseModel,Field
from pydantic import validator
from typing import Optional,List, Any
from botocore.exceptions import ClientError
import sqlite3
import logging
import colorlog
from .db import create_tables,get_node_by_id,get_edge_by_id,db_create_node,db_get_rows_not_in_list,db_delete_row,db_create_edge

from GraphIaC.aws.route53 import HostedZone
from GraphIaC.aws.certificate import Certificate,CertificateHostedZoneEdge,get_dns_validation

from GraphIaC.model_map import BASE_MODEL_MAP

from .logs import setup_logger

logger = setup_logger()


class GraphIaCState(BaseModel):
    session: Any = Field(default=None, exclude=True)  
    db_conn: Any = Field(default=None, exclude=True)  
    G: Any = Field(default=None, exclude=True)
    models_map: dict 
    class Config:
        arbitrary_types_allowed = True  # Allow custom types like sqlite3.Connection

    
    @validator('db_conn')
    def validate_db_conn(cls, v):
        if not isinstance(v, sqlite3.Connection):
            raise ValueError('db_conn must be a valid sqlite3.Connection')
        return v    
    
def init(session,db_conn):
    create_tables(db_conn)

    cursor = db_conn.execute("PRAGMA database_list;")
    rows = cursor.fetchall()

    return GraphIaCState(session=session,db_conn=db_conn,G=nx.DiGraph(),models_map=BASE_MODEL_MAP)


def add_node(state,node):
    state.G.add_node(node.g_id, data=node)


def add_edge(state,a,b,edge):
    state.G.add_edge(a.g_id,b.g_id,data=edge)
    

class OperationType(Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    IMPORT = "import"
    CREATE_EDGE = "create_edge"
    
class Operation(BaseModel):
    operation: OperationType
    obj: Any = Field(default=None, exclude=True)
    


def load_model_from_db(state,obj_name,obj_data):
    pm = state.models_map[obj_name]
        

    return pm.model_validate_json(obj_data)
    
def plan(state):
    """Get all nodes, load from db, diff and plan"""

    plan_ops = []
    db_nodes_seen = []
    db_edges_seen = []
    
    for node in state.G.nodes:
        print(f"Node: {node}")

        pn = state.G.nodes[node]['data']
        print(f"PN:{pn}")
        print(pn.g_id)

        current_state = pn.read(state.session,state.G,g_id=pn.g_id,read_id=pn.read_id)
        
        if not current_state:
            logger.info("Doesn't exist in AWS")
            
            create_op = Operation(operation=OperationType.CREATE,obj=pn)
            plan_ops.append(create_op)
            continue
        
        #it exists in aws does it exist in db and is it different
        pn_db_row = get_node_by_id(state.db_conn,pn.g_id)
        print("DB ROW:",pn_db_row)
        if not pn_db_row:
            #add to db
            create_op = Operation(operation=OperationType.IMPORT,obj=pn)
            plan_ops.append(create_op)
            continue
            

        db_nodes_seen.append(str(pn_db_row[0]))
        pn_last = load_model_from_db(state,pn_db_row[2],pn_db_row[3])
        
        #diff with saved state
        print("DIFF")
        if pn.diff(state.session,state.G,current_state) or pn.diff(state.session,state.G,pn_last):
            print("Update needed")
            update_op = Operation(operation=OperationType.UPDATE,obj=pn)
            plan_ops.append(update_op)

            
        state.G.nodes[node]['data'] = current_state


    #for node in state.G.nodes:
    for edge in list(state.G.edges(data=True)):
        
        print(f"Run EDGE updates: {edge}")
        edge_data = edge[2]["data"]
        print(edge_data)
        source_id = get_node_by_id(state.db_conn,edge[0])[0]
        destination_id = get_node_by_id(state.db_conn,edge[1])[0]
        
        edge_id = get_edge_by_id(state.db_conn,source_id,destination_id)
        print("EDGE id:",edge_id)

        if not edge_id:
            logger.info(f"CREATE EDGE: {edge_data.source_g_id} -> {edge_data.destination_g_id}")
            create_op = Operation(operation=OperationType.CREATE_EDGE,obj=edge_data)
            
            plan_ops.append(create_op)
            
        """
        for e in state.G.neighbors(node):
            
            print(f"\tEdge: {e}")
            edge = state.G.edges[node,e]
            edge_data = state.G.edges[node,e]['data']
            #get edge_id
            edge_node_db_row = get_node_by_id(state.db_conn,e)
            #print(f"\t{edge}")
            print("EN:",edge_node_db_row)
            if edge_node_db_row:
                edge_db_row = get_edge_by_id(state.db_conn,pn_db_row[0],edge_node_db_row[0])
                print(edge_db_row)
            else: # need to create
                print("CREATE EDGE:",edge_data)
                create_op = Operation(operation=OperationType.CREATE_EDGE,obj=pn)
                plan_ops.append(create_op)
           """     
            

    #check for deleted items
    for orphaned_node in db_get_rows_not_in_list(state.db_conn, "nodes", db_nodes_seen):
        print("ORPHANDED Node:",orphaned_node)
        on_last = load_model_from_db(state,orphaned_node[2],orphaned_node[3])
        print(on_last)

        delete_op = Operation(operation=OperationType.DELETE,obj=on_last)
        plan_ops.append(delete_op)
        
    return plan_ops

def run(state):

    changes = plan(state)
    
    for change in changes:
        print(change)

        if change.operation == OperationType.CREATE:
            print("CREATE")
            logger.info(f"Create: {change.obj}")
            result = change.obj.create(state.session,state.G)
            print(result)
            print(change.obj)
            #need to read crrent version and save that
            db_create_node(state.db_conn,change.obj)

        elif change.operation == OperationType.IMPORT:
            #add the obj to the db
            print(f"IMPORT DB  {change.obj}")
            db_create_node(state.db_conn,change.obj)            
        elif change.operation == OperationType.UPDATE:
            print(f"Update: {change.obj}")
            change.obj.update(state.session,state.G)
        elif change.operation == OperationType.DELETE:
            print(f"Delete: {change.obj}")
            result = change.obj.delete(state.session,state.G)
            print(result)
            row_id = get_node_by_id(state.db_conn,change.obj.g_id)
            db_delete_row(state.db_conn,"nodes",row_id)
        elif change.operation == OperationType.CREATE_EDGE:
            print(f"Create EDGE: {change.obj}")
            db_create_edge(state.db_conn,change.obj.source_g_id,change.obj.destination_g_id,change.obj)
            
        


def run_import(state,db_conn,imports):

    for i in imports:
        print(f"Importing {i}")
        db_create_node(db_conn,i)


def export_graph(state,file_name):
    #https://github.com/daniellawrence/graphviz-aws
    A = nx.nx_agraph.to_agraph(state.G)  # convert to a graphviz graph
    A.write(f"{file_name}.dot")  # write to dot file

    A.draw(f"{file_name}.png", prog="neato")    


def walk_graph(G):
    for node in G.nodes:
        print(f"Node: {node}")

        pn = G.nodes[node]['data']

        if not pn.exists(session):
            #create
            print("create")
            pn.create(session,G)
        """    
        for e in G.neighbors(node):
            
            print(f"\tEdge: {e}")
            edge = G.edges[node,e]
            print(f"\t{edge}")
        """





