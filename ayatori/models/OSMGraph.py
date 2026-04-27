import pyrosm
import numpy as np
import time as tm
import rustworkx as rx
from geopy.exc import GeocoderServiceError
from geopy.geocoders import Nominatim


class OSMGraph:
    def __init__(self, OSM_PATH="."):
        self.node_coords = {}
        self._node_id_to_idx = {}
        self._idx_to_node_id = {}
        self.graph = self.create_osm_graph(OSM_PATH)

    def download_osm_file(self, OSM_PATH):
        fp = pyrosm.get_data("Santiago", update=True, directory=OSM_PATH)
        return fp

    def create_osm_graph(self, OSM_PATH):
        fp = self.download_osm_file(OSM_PATH)
        osm = pyrosm.OSM(fp)
        nodes, edges = osm.get_network(nodes=True)

        graph = rx.PyGraph()

        for index, row in nodes.iterrows():
            lon = row["lon"]
            lat = row["lat"]
            node_id = row["id"]
            graph_id = index
            self.node_coords[node_id] = (lat, lon)

            idx = graph.add_node({
                "lon": lon, "lat": lat, "graph_id": graph_id, "node_id": node_id
            })
            self._node_id_to_idx[node_id] = idx
            self._idx_to_node_id[idx] = node_id

        for index, row in edges.iterrows():
            source_node = row["u"]
            target_node = row["v"]

            if row["length"] < 2 or source_node == "" or target_node == "":
                continue
            if source_node not in self._node_id_to_idx or target_node not in self._node_id_to_idx:
                continue

            source_coords = self.node_coords[source_node]
            target_coords = self.node_coords[target_node]
            distance = np.linalg.norm(np.array(source_coords) - np.array(target_coords))

            graph.add_edge(
                self._node_id_to_idx[source_node],
                self._node_id_to_idx[target_node],
                {"u": source_node, "v": target_node, "length": row["length"], "weight": distance},
            )

        return graph

    def get_nodes_and_edges(self):
        nodes = list(self._node_id_to_idx.keys())
        edges = [
            (self._idx_to_node_id[u], self._idx_to_node_id[v])
            for u, v in self.graph.edge_list()
        ]
        return nodes, edges

    def print_graph(self):
        print("Vertices:")
        for idx in self.graph.node_indices():
            data = self.graph[idx]
            print(f"Node ID: {data['node_id']}, lon: {data.get('lon')}, lat: {data.get('lat')}")

        print("\nEdges:")
        for u_idx, v_idx in self.graph.edge_list():
            source = self._idx_to_node_id[u_idx]
            target = self._idx_to_node_id[v_idx]
            print(f"Edge: {source} -> {target}")

    def find_node_by_coordinates(self, lon, lat):
        for idx in self.graph.node_indices():
            data = self.graph[idx]
            if data.get("lon") == lon and data.get("lat") == lat:
                return self._idx_to_node_id[idx]
        return None

    def find_node_by_id(self, node_id):
        if node_id in self._node_id_to_idx:
            return node_id
        return None

    def find_nearest_node(self, latitude, longitude):
        query_point = np.array([longitude, latitude])

        node_ids = list(self._node_id_to_idx.keys())
        coords = []
        for nid in node_ids:
            data = self.graph[self._node_id_to_idx[nid]]
            coords.append([data.get("lon"), data.get("lat")])

        coords = np.array(coords)
        distances = np.linalg.norm(coords - query_point, axis=1)
        nearest_index = np.argmin(distances)
        return node_ids[nearest_index]

    def address_locator(self, address):
        geolocator = Nominatim(user_agent="ayatori")
        while True:
            try:
                location = geolocator.geocode(address)
                break
            except GeocoderServiceError:
                i = 0
                if i < 15:
                    tm.sleep(5)
                    i += 1
                else:
                    return
        if location is not None:
            lat, lon = location.latitude, location.longitude
            nearest = self.find_nearest_node(lat, lon)
            return nearest
        return None
