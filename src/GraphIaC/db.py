
import sqlite3
import json

from pydantic import BaseModel, Field
from typing import Any
#load from db
#dog = Dog.model_validate(from_json(partial_dog_json, allow_partial=True))
"""
model_from_json = MyModel.parse_raw(retrieved_json)
print("Model from JSON (using parse_raw):", model_from_json)

# Method 2: Using `parse_obj` if you load JSON into a Python dictionary first
model_dict = json.loads(retrieved_json)
model_from_dict = MyModel.parse_obj(model_dict)
print("Model from JSON (using parse_obj):", model_from_dict)

"""
from .logs import setup_logger

logger = setup_logger()


#Todo
#turn tables into Pydantic models


class TableNode(BaseModel):
    id: int | None = Field(default=None)
    g_id: str
    type: str
    data: Any
    __tablename__ = 'nodes'

    @classmethod
    def create_table_sql(cls) -> str:
        return """
        CREATE TABLE IF NOT EXISTS nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            g_id TEXT UNIQUE,
            type TEXT,
            data JSON
        );
        """    
class TableEdge(BaseModel):
    id: int | None = Field(default=None)
    g_id: str | None = None        # UNIQUE text ID
    source: int                    # FK → nodes.id
    destination: int               # FK → nodes.id
    weight: float | None = None
    type: str | None = None
    data: Any = None               # JSON payload

    @classmethod
    def create_table_sql(cls) -> str:
        return """
        CREATE TABLE IF NOT EXISTS edges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            g_id TEXT UNIQUE,
            source INTEGER NOT NULL,
            destination INTEGER NOT NULL,
            weight REAL,
            type TEXT,
            data JSON,
            FOREIGN KEY (source) REFERENCES nodes(id) ON DELETE CASCADE,
            FOREIGN KEY (destination) REFERENCES nodes(id) ON DELETE CASCADE,
            UNIQUE(source, destination)
        );
        """

    
def create_tables(conn):
    cursor = conn.cursor()

    sql = TableNode.create_table_sql()
    cursor.execute(sql)

    sql = TableEdge.create_table_sql()
    cursor.execute(sql)

    conn.commit()


    
def db_create_node(conn,node):

    node_name = node.g_id
    node_type = node.__class__.__name__
    node_data = node.model_dump_json()

    cursor = conn.cursor()
    try:
        # Insert a new row into the table
        cursor.execute('''
            INSERT INTO nodes (g_id, type, data)
            VALUES (?, ?, ?)
        ''', (node_name, node_type, node_data))
        
        # Commit the transaction
        conn.commit()
        print("Row inserted successfully.")

    except sqlite3.IntegrityError as e:
        # Handle unique constraint failure for the name field
        print(f"Error: {e}")
    
    finally:
        #conn.close()
        pass

def get_node_by_id(conn,name):
    cursor = conn.cursor()
    try:
        # Execute the query to find the node's ID by name
        cursor.execute('''
            SELECT * FROM nodes WHERE g_id = ?
        ''', (name,))
        
        # Fetch the result
        result = cursor.fetchone()

        # Return the ID if the node is found, otherwise None
        if result:
            return result
        else:
            print(f"Node '{name}' does not exist.")
            return None
    
    except sqlite3.IntegrityError as e:
        # Handle unique constraint failure for the name field
        print(f"Error: {e}")

def get_edge_by_id(conn,s_name,d_name):
    cursor = conn.cursor()
    try:
        # Execute the query to find the node's ID by name
        cursor.execute('''
            SELECT * FROM edges WHERE source = ? and destination = ?
        ''', (s_name,d_name))
        
        # Fetch the result
        result = cursor.fetchone()

        # Return the ID if the node is found, otherwise None
        if result:
            return result
        else:
            print(f"Node '{s_name}' does not exist.")
            return None
    
    except sqlite3.IntegrityError as e:
        # Handle unique constraint failure for the name field
        print(f"Error: {e}")

        
def db_create_edge(conn,source_name, destination_name, edge,weight=1):
    edge_type = edge.__class__.__name__
    edge_data = edge.model_dump_json()

    source = get_node_by_id(conn,source_name)[0]
    destination = get_node_by_id(conn,destination_name)[0]

    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT INTO edges (source, destination, weight, type,data)
            VALUES (?, ?, ?, ?, ?)
        ''', (source, destination, weight, edge_type,edge_data))
        
        conn.commit()
        logger.debug("Edge added successfully.")
    
    except sqlite3.IntegrityError as e:
        print(f"Error: {e}")
    
    finally:
        #conn.close()
        pass


def name_exists(name):
    # Connect to the SQLite database
    #conn = sqlite3.connect("example.db")
    cursor = conn.cursor()
    
    # Execute a query to check for the existence of the name
    cursor.execute('''
        SELECT EXISTS(SELECT 1 FROM my_table WHERE g_id = ?)
    ''', (name,))
    
    # Fetch the result (0 or 1)
    exists = cursor.fetchone()[0]
    
    # Close the connection
    #conn.close()
    
    # Return True if name exists, otherwise False
    return exists == 1

def db_get_rows_not_in_list(conn, table_name, id_list):

    cursor = conn.cursor()

    
    query = f"SELECT * FROM {table_name} WHERE id NOT IN ({",".join(id_list)})"

    print(query)
    try:
        # Execute the query and pass the id_tuple as parameters
        cursor.execute(query)
        
        # Fetch all results
        return cursor.fetchall()
        

    finally:
        # Close the connection
        #conn.close()
        pass


# delete row



def db_delete_row(db_conn, table_name, row_id):
    """
    Delete a row from a specified SQLite table based on the ID.

    Parameters:
    - db_path: str - Path to the SQLite database file.
    - table_name: str - Name of the table from which to delete the row.
    - row_id: int - ID of the row to delete.
    """
    try:
        # Connect to the SQLite database

        cursor = db_conn.cursor()

        # Define the SQL query to delete the row
        sql_query = f"DELETE FROM {table_name} WHERE id = ?"

        # Execute the query with the provided ID
        cursor.execute(sql_query, (row_id,))
        
        # Commit the transaction
        conn.commit()

        print(f"Row with ID {row_id} deleted successfully from '{table_name}' table.")

    except sqlite3.Error as e:
        print(f"Error deleting row from '{table_name}' table: {e}")
    
    finally:
        # Close the connection
        #conn.close()
        pass

            
