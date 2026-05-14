import time as tm

import numpy as np
import pyrosm
import rustworkx as rx
from geopy.exc import GeocoderServiceError
from geopy.geocoders import Nominatim
from scipy.spatial import cKDTree


class OSMGraph:
    def __init__(self, OSM_PATH="."):
        self.node_coords = {}
        self._node_id_to_idx = {}
        self._idx_to_node_id = {}
        self._node_id_list: list = []
        self._coord_array: np.ndarray = np.empty((0, 2))
        self._kdtree: cKDTree = None
        self.graph = self.create_osm_graph(OSM_PATH)
        self._build_spatial_index()

    def _build_spatial_index(self):
        """Construye un KDTree sobre las coordenadas de los nodos OSM para
        búsquedas espaciales O(log n).
        """
        if not self.node_coords:
            self._kdtree = None
            return
        self._node_id_list = list(self.node_coords.keys())
        # node_coords almacena (lat, lon); el KDTree consulta en el mismo orden.
        self._coord_array = np.asarray(
            [self.node_coords[nid] for nid in self._node_id_list],
            dtype=np.float64,
        )
        self._kdtree = cKDTree(self._coord_array)

    @classmethod
    def from_file(cls, pbf_path: str, network_type: str = "walking") -> "OSMGraph":
        """
        Carga la red OSM desde un archivo .pbf ya descargado, sin descargar nada.
        Útil para reutilizar archivos como Santiago.osm.pbf.

        Args:
            pbf_path: Ruta al archivo .pbf
            network_type: Tipo de red ("walking", "cycling", "driving", "all")

        Returns:
            Instancia de OSMGraph inicializada
        """
        instance = cls.__new__(cls)
        instance.node_coords = {}
        instance._node_id_to_idx = {}
        instance._idx_to_node_id = {}
        instance._node_id_list = []
        instance._coord_array = np.empty((0, 2))
        instance._kdtree = None

        osm = pyrosm.OSM(pbf_path)
        nodes, edges = osm.get_network(network_type=network_type, nodes=True)

        graph = rx.PyGraph()
        node_id_to_idx, node_coords = instance._populate_graph(graph, nodes, edges)
        instance._node_id_to_idx = node_id_to_idx
        instance._idx_to_node_id = {idx: nid for nid, idx in node_id_to_idx.items()}
        instance.node_coords = node_coords
        instance.graph = graph
        instance._build_spatial_index()
        return instance

    @staticmethod
    def _populate_graph(graph, nodes, edges):
        """Llena un PyGraph rustworkx con nodos+aristas del DataFrame pyrosm."""
        node_id_to_idx = {}
        node_coords = {}

        for index, row in nodes.iterrows():
            lon = row["lon"]
            lat = row["lat"]
            node_id = row["id"]
            node_coords[node_id] = (lat, lon)

            idx = graph.add_node(
                {
                    "lon": lon,
                    "lat": lat,
                    "graph_id": index,
                    "node_id": node_id,
                }
            )
            node_id_to_idx[node_id] = idx

        for _, row in edges.iterrows():
            source_node = row["u"]
            target_node = row["v"]
            length = row["length"]

            if length < 2 or not source_node or not target_node:
                continue
            if source_node not in node_id_to_idx or target_node not in node_id_to_idx:
                continue

            graph.add_edge(
                node_id_to_idx[source_node],
                node_id_to_idx[target_node],
                {"u": source_node, "v": target_node, "length": length, "weight": length},
            )

        return node_id_to_idx, node_coords

    def download_osm_file(self, OSM_PATH):
        fp = pyrosm.get_data("Santiago", update=True, directory=OSM_PATH)
        return fp

    def create_osm_graph(self, OSM_PATH):
        fp = self.download_osm_file(OSM_PATH)
        osm = pyrosm.OSM(fp)
        nodes, edges = osm.get_network(nodes=True)

        graph = rx.PyGraph()
        node_id_to_idx, node_coords = self._populate_graph(graph, nodes, edges)
        self._node_id_to_idx = node_id_to_idx
        self._idx_to_node_id = {idx: nid for nid, idx in node_id_to_idx.items()}
        self.node_coords = node_coords
        return graph

    def get_nodes_and_edges(self):
        nodes = list(self._node_id_to_idx.keys())
        edges = [
            (self._idx_to_node_id[u], self._idx_to_node_id[v]) for u, v in self.graph.edge_list()
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
        for nid, (n_lat, n_lon) in self.node_coords.items():
            if n_lon == lon and n_lat == lat:
                return nid
        return None

    def find_node_by_id(self, node_id):
        if node_id in self._node_id_to_idx:
            return node_id
        return None

    def find_nearest_node(self, latitude, longitude):
        """Nodo OSM más cercano usando KDTree (O(log n))."""
        if self._kdtree is None:
            return None
        _, idx = self._kdtree.query([latitude, longitude])
        return self._node_id_list[idx]

    def address_locator(self, address, max_retries: int = 15, retry_delay: float = 5.0):
        """Geocodifica una dirección y devuelve el nodo OSM más cercano.

        Reintenta hasta ``max_retries`` veces si el servicio Nominatim falla.
        """
        geolocator = Nominatim(user_agent="ayatori")
        location = None
        for _ in range(max_retries):
            try:
                location = geolocator.geocode(address)
                break
            except GeocoderServiceError:
                tm.sleep(retry_delay)
        if location is None:
            return None
        return self.find_nearest_node(location.latitude, location.longitude)
