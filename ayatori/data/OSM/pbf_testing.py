# import the necessary packages
import geopandas as gpd
import pandas as pd

# import pyrosm functions
from pyrosm import OSM
from pyrosm import get_data

# network libraries
import networkx as nx
import matplotlib.pyplot as plt


def get_network_custom(place, filter_type):
    """
    place: string
        name of the pbf to be downloaded

    filter_type: string
        'cycling' only for now
    """
    # connect to the data
    osm = OSM(get_data(place))

    # if you have already downloaded pbf file and it’s in your file directory use following
    # osm = OSM(place)
    # where place is 'filepath.pbf'

    # retrieve the complete network
    nodes, edges = osm.get_network(nodes=True, network_type="all")

    # define the filter
    if filter_type == "cycling":
        highway = [
            "primary",
            "primary_link",
            "trunk",
            "trunk_link",
            "secondary",
            "secondary_link",
            "tertiary",
            "tertiary_link",
            "unclassified",
            "residential",
            "living_street",
            "road",
            "service",
            "track",
            "path",
            "pedestrian",
            "footway",
            "bridleway",
            "cycleway",
            "busway",
        ]
    else:
        raise ValueError("try cycling")
    # you could define other profiles such as driving or walking

    # choose only those edges that are inside the filter
    edges = edges[edges["highway"].isin(highway)]

    # list origin nodes
    list_origins = list(edges.u.unique())

    # list destination nodes
    list_destinations = list(edges.v.unique())

    # combine the to lists to see all nodes that exist in filtered network
    list_nodes = list_origins + list_destinations

    # cut the nodes by the existing list
    nodes_gdf = nodes[nodes.id.isin(list_nodes)]

    return nodes_gdf, edges


nodes, edges = get_network_custom("Bristol", "cycling")
ax = edges.plot()
nodes.plot(ax=ax, color="orange", markersize=2)
plt.show()
