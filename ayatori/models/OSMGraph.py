import math

import numpy as np
import osmium
import rustworkx as rx
from scipy.spatial import cKDTree

# Tipos de vía caminables de OSM (se excluyen autopistas y enlaces de motorway).
WALK_HIGHWAYS = {
    "footway", "path", "pedestrian", "steps", "living_street", "residential",
    "service", "unclassified", "tertiary", "secondary", "primary", "track",
    "cycleway", "road", "trunk", "primary_link", "secondary_link",
    "tertiary_link", "trunk_link",
}


def _haversine_m(la, lo, lb, lo2):
    R = 6371000.0
    p1, p2 = math.radians(la), math.radians(lb)
    dp, dl = math.radians(lb - la), math.radians(lo2 - lo)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


class _WalkNetHandler(osmium.SimpleHandler):
    """Extrae la red peatonal de un .pbf: nodos (lat/lon) y aristas con largo."""

    def __init__(self):
        super().__init__()
        self.coords = {}   # node_id -> (lat, lon)
        self.edges = []    # (u, v, length_m)

    def way(self, w):
        if w.tags.get("highway") not in WALK_HIGHWAYS:
            return
        nd = w.nodes
        for i in range(len(nd) - 1):  # WayNodeList no soporta slicing
            a, b = nd[i], nd[i + 1]
            if not a.location.valid() or not b.location.valid():
                continue
            la, lo = a.location.lat, a.location.lon
            lb, lo2 = b.location.lat, b.location.lon
            self.coords[a.ref] = (la, lo)
            self.coords[b.ref] = (lb, lo2)
            self.edges.append((a.ref, b.ref, _haversine_m(la, lo, lb, lo2)))


class OSMGraph:
    def __init__(self, pbf_path: str = None):
        self._init_fields()
        if pbf_path:
            self._load_pbf(pbf_path)

    def _init_fields(self):
        self.node_coords = {}
        self._node_id_to_idx = {}
        self._idx_to_node_id = {}
        self._node_id_list = []
        self._coord_array = np.empty((0, 2))
        self._kdtree = None
        self._sp_cache = {}  # (src_idx, tgt_idx) -> (km, polyline) | None
        self.graph = rx.PyGraph()

    def _load_pbf(self, pbf_path: str):
        h = _WalkNetHandler()
        h.apply_file(pbf_path, locations=True)  # locations=True rellena lat/lon en las ways

        graph = rx.PyGraph()
        idx = {}
        for nid, (lat, lon) in h.coords.items():
            idx[nid] = graph.add_node({"lat": lat, "lon": lon, "node_id": nid})
        for u, v, length in h.edges:
            if length < 2:
                continue
            # Payload = float pelado: Dijkstra llama weight_fn por cada arista
            # relajada; un dict aquí costaba un lookup extra por llamada.
            graph.add_edge(idx[u], idx[v], length)

        self.graph = graph
        self._node_id_to_idx = idx
        self._idx_to_node_id = {i: nid for nid, i in idx.items()}
        self.node_coords = h.coords
        self._build_spatial_index()

    def _build_spatial_index(self):
        """KDTree sobre las coordenadas de los nodos OSM para búsquedas O(log n)."""
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

    def find_nearest_node(self, latitude, longitude):
        """Nodo OSM más cercano usando KDTree (O(log n))."""
        if self._kdtree is None:
            return None
        _, idx = self._kdtree.query([latitude, longitude])
        return self._node_id_list[idx]

    def shortest_path(self, from_latlon, to_latlon):
        """Ruta peatonal más corta entre dos coordenadas por la red OSM.

        Args:
            from_latlon: ``(lat, lon)`` de origen.
            to_latlon: ``(lat, lon)`` de destino.

        Returns:
            ``(distancia_km, polyline)`` donde ``polyline`` es ``[[lat, lon], ...]``,
            o ``None`` si no hay ruta (componentes desconectadas, sin índice).
            El llamador cae a Haversine cuando recibe ``None``.
        """
        if self._kdtree is None:
            return None
        fid = self.find_nearest_node(from_latlon[0], from_latlon[1])
        tid = self.find_nearest_node(to_latlon[0], to_latlon[1])
        if fid is None or tid is None:
            return None
        src = self._node_id_to_idx.get(fid)
        tgt = self._node_id_to_idx.get(tid)
        if src is None or tgt is None:
            return None

        if src == tgt:
            d = self.graph[src]
            return (0.0, [[d["lat"], d["lon"]], [d["lat"], d["lon"]]])

        ck = (src, tgt)
        if ck in self._sp_cache:
            return self._sp_cache[ck]

        try:
            paths = rx.dijkstra_shortest_paths(
                self.graph,
                src,
                target=tgt,
                weight_fn=float,
            )
        except Exception:
            self._sp_cache[ck] = None
            return None

        # rustworkx PathMapping no tiene .get(); indexar con guarda de pertenencia.
        node_path = list(paths[tgt]) if tgt in paths else None
        if not node_path:
            self._sp_cache[ck] = None
            return None

        meters = 0.0
        poly = []
        prev = None
        for idx in node_path:
            d = self.graph[idx]
            poly.append([d["lat"], d["lon"]])
            if prev is not None:
                ed = self.graph.get_edge_data(prev, idx)  # payload = largo en metros
                if ed is not None:
                    meters += ed
            prev = idx

        result = (meters / 1000.0, poly)
        if len(self._sp_cache) < 200000:  # cota de memoria
            self._sp_cache[ck] = result
        return result
