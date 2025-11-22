import pyrosm
import numpy as np
import time as tm
import networkx as nx
from geopy.exc import GeocoderServiceError
from geopy.geocoders import Nominatim


class OSMGraph(nx.Graph):
    def __init__(self, OSM_PATH="."):
        super().__init__()
        self.node_coords = {}
        self.graph = self.create_osm_graph(OSM_PATH)

    def download_osm_file(self, OSM_PATH):
        """
        Downloads the latest OSM file for Santiago.

        Parameters:
            OSM_PATH (str): The directory where the OSM file will be saved.

        Returns:
            str: The path to the downloaded OSM file.
        """
        fp = pyrosm.get_data("Santiago", update=True, directory=OSM_PATH)

        return fp

    def create_osm_graph(self, OSM_PATH):
        """
        Creates a networkx graph using the downloaded OSM data for Santiago.

        Returns:
            graph: osm data converted to a graph
        """
        # Download latest OSM data
        fp = self.download_osm_file(OSM_PATH)

        osm = pyrosm.OSM(fp)

        nodes, edges = osm.get_network(nodes=True)

        graph = nx.Graph()

        print("GETTING OSM NODES...")
        for index, row in nodes.iterrows():
            lon = row["lon"]
            lat = row["lat"]
            node_id = row["id"]
            graph_id = index
            self.node_coords[node_id] = (lat, lon)

            # Add node with attributes for lon, lat, node_id, and graph_id
            graph.add_node(node_id, lon=lon, lat=lat, graph_id=graph_id)

        print("DONE")
        print("GETTING OSM EDGES...")

        for index, row in edges.iterrows():
            source_node = row["u"]
            target_node = row["v"]

            if row["length"] < 2 or source_node == "" or target_node == "":
                continue  # Skip edges with empty or missing nodes

            if source_node not in graph or target_node not in graph:
                print(f"Skipping edge with missing nodes: {source_node} -> {target_node}")
                continue  # Skip edges with missing nodes

            # Calculate the distance between the nodes and use it as the weight of the edge
            source_coords = self.node_coords[source_node]
            target_coords = self.node_coords[target_node]
            distance = np.linalg.norm(np.array(source_coords) - np.array(target_coords))

            graph.add_edge(
                source_node,
                target_node,
                u=source_node,
                v=target_node,
                length=row["length"],
                weight=distance,
            )

        print("OSM DATA HAS BEEN SUCCESSFULLY RECEIVED")
        return graph

    def get_nodes_and_edges(self):
        """
        Returns a tuple containing two lists: one with the nodes and another with the edges.
        """
        nodes = list(self.graph.nodes())
        edges = list(self.graph.edges())
        return nodes, edges

    def print_graph(self):
        """
        Prints the vertices and edges of the graph.
        """
        print("Vertices:")
        for node in self.graph.nodes():
            attrs = self.graph.nodes[node]
            print(f"Node ID: {node}, lon: {attrs.get('lon')}, lat: {attrs.get('lat')}")

        print("\nEdges:")
        for source, target in self.graph.edges():
            print(f"Edge: {source} -> {target}")

    def find_node_by_coordinates(self, lon, lat):
        """
        Finds a node in the graph based on its coordinates (lon, lat).

        Parameters:
            lon (float): the longitude of the node.
            lat (float): the latitude of the node.

        Returns:
            node: the node in the graph with the specified coordinates, or None if not found.
        """
        for node in self.graph.nodes():
            attrs = self.graph.nodes[node]
            if attrs.get("lon") == lon and attrs.get("lat") == lat:
                return node
        return None

    def find_node_by_id(self, node_id):
        """
        Finds a node in the graph based on its id.

        Parameters:
            node_id (long): the id of the node.

        Returns:
            node: the node in the graph with the specified id, or None if not found.
        """
        if node_id in self.graph.nodes():
            return node_id
        return None

    def find_nearest_node(self, latitude, longitude):
        """
        Finds the nearest node in the graph to a given set of coordinates.

        Parameters:
            latitude (float): the latitude of the coordinates.
            longitude (float): the longitude of the coordinates.

        Returns:
            node: the node in the graph closest to the given coordinates.
        """
        query_point = np.array([longitude, latitude])

        # Extract lon/lat from all nodes
        nodes = list(self.graph.nodes())
        coords = []
        for node in nodes:
            attrs = self.graph.nodes[node]
            coords.append([attrs.get("lon"), attrs.get("lat")])

        coords = np.array(coords)

        # Calculates the euclidean distances between the node's coordinates and the consulted address's coordinates
        distances = np.linalg.norm(coords - query_point, axis=1)

        # Finds the nearest node's index
        nearest_node_index = np.argmin(distances)
        nearest_node = nodes[nearest_node_index]

        return nearest_node

    def address_locator(self, address):
        """
        Finds the given address in the OSM graph.

        Parameters:
        address (str): The address to be located.

        Returns:
        int: The ID of the nearest vertex in the graph.

        Raises:
        GeocoderServiceError: If there is an error with the geocoding service.
        """
        geolocator = Nominatim(user_agent="ayatori")
        while True:
            try:
                location = geolocator.geocode(address)
                break
            except GeocoderServiceError:
                i = 0
                if i < 15:
                    print("Geocoding service error. Retrying in 5 seconds...")
                    tm.sleep(5)
                    i += 1
                else:
                    msg = "Error: Too many retries. Geocoding service may be down. Please try again later."
                    print(msg)
                    return
        if location is not None:
            lat, lon = location.latitude, location.longitude
            nearest = self.find_nearest_node(lat, lon)
            return nearest
        msg = "Error: Address couldn't be found."
        print(msg)
