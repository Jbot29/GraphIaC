
import networkx as nx



G=nx.DiGraph()


a = "A"

G.add_node(a)


b = "B"

G.add_node(b)

G.add_edge(a,b, color='red',edge_type=b)





for node in G.nodes:
    print(f"Node: {node}")

    for e in G.neighbors(node):
        print(e)


incoming_edges = G.in_edges(b)

# Print the incoming edges
print(f"Incoming edges to node {b}: {list(incoming_edges)}")


edge = G.edges[a,b]

print(edge)


"""
G[1][2]['weight'] = 5  # Modify the weight attribute of edge (1, 2)
G[2][3]['color'] = 'blue' # Modify the color attribute of edge (2, 3)

"""
