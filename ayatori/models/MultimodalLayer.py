"""
Capa multimodal unificada: conecta datos GTFS (tránsito) con OSM (calles).
Proporciona una interfaz única para consultas de paradas y tiempos de caminata.
"""

from typing import List, Tuple, Optional
from math import radians, sin, cos, sqrt, atan2


class MultimodalLayer:
    """
    Coordina datos de tránsito (GTFS) y red vial (OSM) en una capa unificada.

    Uso mínimo (solo tránsito):
        layer = MultimodalLayer(gtfs_data)

    Uso completo (tránsito + calles):
        layer = MultimodalLayer(gtfs_data, osm_graph)

    Con la red OSM presente, los tiempos de caminata pueden calcularse sobre la
    topología real de calles en lugar de distancia en línea recta.
    """

    def __init__(self, gtfs_data, osm_graph=None):
        """
        Args:
            gtfs_data: Instancia de GTFSData
            osm_graph: Instancia de OSMGraph (opcional)
        """
        self.gtfs = gtfs_data
        self.osm = osm_graph

    # ------------------------------------------------------------------ #
    # Propiedades de estado                                                #
    # ------------------------------------------------------------------ #

    @property
    def has_street_network(self) -> bool:
        """True si hay red vial OSM disponible."""
        return self.osm is not None

    # ------------------------------------------------------------------ #
    # Consultas de paradas                                                 #
    # ------------------------------------------------------------------ #

    def get_nearby_stops(
        self,
        coords: Tuple[float, float],
        radius_km: float = 0.5,
        max_stops: int = 10,
    ) -> List[Tuple[str, float]]:
        """
        Paradas de tránsito cercanas a una posición.

        Args:
            coords: (lat, lon)
            radius_km: Radio de búsqueda en km
            max_stops: Número máximo de resultados

        Returns:
            [(stop_id, distance_km), ...] ordenado por distancia ascendente
        """
        return self.gtfs.get_nearby_stops(coords, margin_km=radius_km, max_stops=max_stops)

    def stops_with_walking_times(
        self,
        origin_coords: Tuple[float, float],
        radius_km: float = 0.5,
        speed_kmh: float = 5.0,
        max_stops: int = 10,
    ) -> List[Tuple[str, float, float]]:
        """
        Paradas cercanas con distancia y tiempo de caminata.

        Args:
            origin_coords: (lat, lon) del punto de origen
            radius_km: Radio de búsqueda en km
            speed_kmh: Velocidad de caminata en km/h
            max_stops: Número máximo de resultados

        Returns:
            [(stop_id, distance_km, walking_time_seconds), ...] por distancia
        """
        nearby = self.get_nearby_stops(origin_coords, radius_km=radius_km, max_stops=max_stops)
        result = []
        for stop_id, distance_km in nearby:
            stop_coords = self.get_stop_coords(stop_id)
            if stop_coords is not None:
                walk_time = self.walking_time_seconds(origin_coords, stop_coords, speed_kmh)
            else:
                walk_time = (distance_km / speed_kmh) * 3600
            result.append((stop_id, distance_km, walk_time))
        return result

    def snap_to_stop(self, coords: Tuple[float, float]) -> Optional[Tuple[str, float]]:
        """
        Parada más cercana a unas coordenadas.

        Returns:
            (stop_id, distance_km) o None
        """
        nearby = self.get_nearby_stops(coords, radius_km=2.0, max_stops=1)
        return nearby[0] if nearby else None

    def get_stop_coords(self, stop_id: str) -> Optional[Tuple[float, float]]:
        """
        Coordenadas (lat, lon) de una parada.

        Returns:
            (lat, lon) o None si no se encuentra
        """
        raw = self.gtfs.get_stop_coords(stop_id)  # devuelve (lon, lat)
        if raw is None:
            return None
        lon, lat = raw
        return (lat, lon)

    # ------------------------------------------------------------------ #
    # Consultas sobre la red OSM                                          #
    # ------------------------------------------------------------------ #

    def snap_to_osm_node(self, coords: Tuple[float, float]) -> Optional[int]:
        """
        Nodo OSM más cercano a unas coordenadas.
        Requiere red OSM. Retorna None si no está disponible.

        Args:
            coords: (lat, lon)

        Returns:
            node_id del nodo OSM más cercano, o None
        """
        if not self.has_street_network:
            return None
        lat, lon = coords
        return self.osm.find_nearest_node(lat, lon)

    # ------------------------------------------------------------------ #
    # Cálculo de caminata                                                  #
    # ------------------------------------------------------------------ #

    def walking_time_seconds(
        self,
        from_coords: Tuple[float, float],
        to_coords: Tuple[float, float],
        speed_kmh: float = 5.0,
    ) -> float:
        """
        Tiempo de caminata entre dos puntos en segundos.

        Usa la red OSM cuando está disponible (distancia real en calle).
        Cae a haversine en línea recta si no hay red OSM.

        Args:
            from_coords: (lat, lon) de origen
            to_coords: (lat, lon) de destino
            speed_kmh: Velocidad de caminata en km/h

        Returns:
            Tiempo en segundos
        """
        if self.has_street_network:
            return self._osm_walking_time(from_coords, to_coords, speed_kmh)
        return self._haversine_walking_time(from_coords, to_coords, speed_kmh)

    def walking_distance_km(
        self,
        from_coords: Tuple[float, float],
        to_coords: Tuple[float, float],
    ) -> float:
        """
        Distancia de caminata en km entre dos puntos (haversine).

        Args:
            from_coords: (lat, lon) de origen
            to_coords: (lat, lon) de destino

        Returns:
            Distancia en km
        """
        lat1, lon1 = from_coords
        lat2, lon2 = to_coords
        return self._haversine(lon1, lat1, lon2, lat2)

    # ------------------------------------------------------------------ #
    # Helpers internos                                                     #
    # ------------------------------------------------------------------ #

    def _haversine_walking_time(
        self,
        from_coords: Tuple[float, float],
        to_coords: Tuple[float, float],
        speed_kmh: float,
    ) -> float:
        lat1, lon1 = from_coords
        lat2, lon2 = to_coords
        distance_km = self._haversine(lon1, lat1, lon2, lat2)
        return (distance_km / speed_kmh) * 3600

    def _osm_walking_time(
        self,
        from_coords: Tuple[float, float],
        to_coords: Tuple[float, float],
        speed_kmh: float,
    ) -> float:
        """
        Tiempo de caminata usando la red de calles OSM (rustworkx dijkstra).
        Cae a haversine si los nodos origen/destino no se encuentran o si
        no hay camino entre ellos.
        """
        try:
            import rustworkx as rx

            src_node = self.snap_to_osm_node(from_coords)
            dst_node = self.snap_to_osm_node(to_coords)

            if src_node is None or dst_node is None:
                return self._haversine_walking_time(from_coords, to_coords, speed_kmh)

            src_idx = self.osm._node_id_to_idx[src_node]
            dst_idx = self.osm._node_id_to_idx[dst_node]

            paths = rx.dijkstra_shortest_paths(
                self.osm.graph,
                src_idx,
                target=dst_idx,
                weight_fn=lambda e: float(e.get("length", e.get("weight", 1.0))),
            )

            if dst_idx not in paths:
                return self._haversine_walking_time(from_coords, to_coords, speed_kmh)

            # Sumar longitudes de aristas del camino encontrado
            path = paths[dst_idx]
            total_m = 0.0
            for i in range(len(path) - 1):
                edge_data = self.osm.graph.get_edge_data(path[i], path[i + 1])
                if edge_data:
                    total_m += edge_data.get("length", 0.0)

            distance_km = total_m / 1000.0
            return (distance_km / speed_kmh) * 3600

        except Exception:
            return self._haversine_walking_time(from_coords, to_coords, speed_kmh)

    @staticmethod
    def _haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        R = 6371.0
        lat1_r, lat2_r = radians(lat1), radians(lat2)
        d_lat = radians(lat2 - lat1)
        d_lon = radians(lon2 - lon1)
        a = sin(d_lat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(d_lon / 2) ** 2
        return R * 2 * atan2(sqrt(a), sqrt(1 - a))

    def __repr__(self):
        osm_status = "con OSM" if self.has_street_network else "sin OSM"
        return f"MultimodalLayer({len(self.gtfs.route_stops)} rutas, {osm_status})"
