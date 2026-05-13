"""
Connection Scan Algorithm con soporte para transbordos
Implementación optimizada para planificación de viajes multimodales
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import List, Optional, Tuple
import heapq

_EARTH_RADIUS_KM = 6371.0
_TRANSFER_PENALTY_SECONDS = 120  # mínimo de 2 minutos para cambiar de ruta


@dataclass(slots=True)
class Journey:
    """
    Representa un viaje completo con múltiples segmentos.
    """
    segments: List[dict]  # Cada segmento puede ser walk, transit, o transfer
    total_duration: timedelta
    departure_time: datetime
    arrival_time: datetime
    number_of_transfers: int
    total_walking_distance: float  # en km
    
    def __lt__(self, other):
        """Para comparación: prefiere menos tiempo total, luego menos transferencias"""
        if self.total_duration != other.total_duration:
            return self.total_duration < other.total_duration
        return self.number_of_transfers < other.number_of_transfers
    
    def __repr__(self):
        hours = self.total_duration.total_seconds() / 3600
        return (f"Journey(duration={hours:.1f}h, transfers={self.number_of_transfers}, "
                f"walk={self.total_walking_distance:.2f}km)")


class ConnectionScanAlgorithm:
    """
    Implementación del Connection Scan Algorithm con soporte para:
    - Múltiples transferencias
    - Caminata (inicio, fin, y entre paradas)
    - Optimización por tiempo de llegada más temprano
    - Rutas alternativas
    """
    
    def __init__(self, gtfs_data, transfer_manager=None,
                 max_walking_distance_km: float = 0.8,
                 walking_speed_kmh: float = 5.0,
                 max_transfers: int = 3):
        """
        Inicializa el algoritmo CSA.
        
        Args:
            gtfs_data: Instancia de GTFSData
            transfer_manager: TransferManager con transferencias precalculadas
            max_walking_distance_km: Distancia máxima de caminata
            walking_speed_kmh: Velocidad de caminata
            max_transfers: Número máximo de transferencias permitidas
        """
        self.gtfs = gtfs_data
        self.transfer_manager = transfer_manager
        self.max_walking_km = max_walking_distance_km
        self.walking_speed = walking_speed_kmh
        self.max_transfers = max_transfers

        # Índice stop→rutas: usa el de GTFSData si ya está construido; lo reconstruye si no
        self._stop_to_routes: dict = getattr(gtfs_data, "_stop_to_routes", None)
        if self._stop_to_routes is None:
            self._stop_to_routes = defaultdict(list)
            for route_id, stops_dict in gtfs_data.route_stops.items():
                for sid in stops_dict:
                    self._stop_to_routes[sid].append(route_id)
    
    def find_journey(self, 
                     origin_coords: Tuple[float, float],
                     destination_coords: Tuple[float, float],
                     departure_time: datetime,
                     num_alternatives: int = 3) -> List[Journey]:
        """
        Encuentra las mejores rutas desde origen a destino.
        
        Args:
            origin_coords: (lat, lon) del origen
            destination_coords: (lat, lon) del destino
            departure_time: Hora de salida
            num_alternatives: Número de rutas alternativas a retornar
            
        Returns:
            Lista de Journey ordenados por calidad (mejor primero)
        """
        # Validación de entrada
        if not isinstance(origin_coords, tuple) or len(origin_coords) != 2:
            raise ValueError("origin_coords debe ser una tupla (lat, lon)")
        if not isinstance(destination_coords, tuple) or len(destination_coords) != 2:
            raise ValueError("destination_coords debe ser una tupla (lat, lon)")
        if not isinstance(departure_time, datetime):
            raise ValueError("departure_time debe ser un objeto datetime")

        # ── Caminata directa ─────────────────────────────────────────────────
        direct_dist = self._haversine(
            origin_coords[1], origin_coords[0],
            destination_coords[1], destination_coords[0],
        )
        direct_walk_journeys: List[Journey] = []

        if direct_dist <= self.max_walking_km:
            walk_time_sec = (direct_dist / self.walking_speed) * 3600
            walk_journey = Journey(
                segments=[
                    {
                        "type": "walk",
                        "from": "origin",
                        "to": "destination",
                        "from_latlon": list(origin_coords),
                        "to_latlon": list(destination_coords),
                        "distance_km": direct_dist,
                        "duration": timedelta(seconds=walk_time_sec),
                        "start_time": departure_time,
                        "end_time": departure_time + timedelta(seconds=walk_time_sec),
                    }
                ],
                total_duration=timedelta(seconds=walk_time_sec),
                departure_time=departure_time,
                arrival_time=departure_time + timedelta(seconds=walk_time_sec),
                number_of_transfers=0,
                total_walking_distance=direct_dist,
            )
            direct_walk_journeys.append(walk_journey)

            # Si el destino está dentro del radio caminable, preferir siempre
            # la caminata directa: no tiene sentido tomar transporte público.
            return direct_walk_journeys
        # ────────────────────────────────────────────────────────────────────

        # Paso 1: Encontrar paradas cercanas al origen (más paradas = más diversidad)
        origin_stops = self.gtfs.get_nearby_stops(
            origin_coords,
            margin_km=self.max_walking_km,
            max_stops=10,
        )

        if not origin_stops:
            return direct_walk_journeys

        # Paso 2: Encontrar paradas cercanas al destino
        destination_stops = self.gtfs.get_nearby_stops(
            destination_coords,
            margin_km=self.max_walking_km,
            max_stops=8,
        )

        if not destination_stops:
            return direct_walk_journeys

        # Paso 3: Ejecutar CSA para combinaciones de paradas
        # Limitar a las más cercanas para no explotar combinaciones
        origin_stops_limited = origin_stops[:6]
        destination_stops_limited = destination_stops[:5]

        all_journeys: List[Journey] = []
        candidate_limit = num_alternatives * 4  # buffer generoso antes de filtrar

        for origin_stop, origin_dist in origin_stops_limited:
            origin_walk_time = (origin_dist / self.walking_speed) * 3600
            arrival_at_origin_stop = departure_time + timedelta(seconds=origin_walk_time)

            for dest_stop, dest_dist in destination_stops_limited:
                journeys = self._connection_scan(
                    origin_stop,
                    dest_stop,
                    arrival_at_origin_stop,
                    origin_coords,
                    destination_coords,
                    origin_dist,
                    dest_dist,
                    departure_time,
                    num_alternatives,
                )
                all_journeys.extend(journeys)

                if len(all_journeys) >= candidate_limit:
                    break
            if len(all_journeys) >= candidate_limit:
                break

        # Paso 4: Ordenar y retornar las mejores rutas
        if not all_journeys and not direct_walk_journeys:
            return []

        all_journeys.sort()

        # Filtrar rutas muy similares (misma secuencia de rutas de tránsito)
        unique_journeys = self._filter_similar_journeys(all_journeys)

        # Aplicar filtro Pareto (tiempo vs transferencias)
        pareto_journeys = self._pareto_filter(unique_journeys)

        # Si el frente de Pareto tiene pocas opciones, completar con alternativas
        # por conjunto de rutas distinto aunque estén dominadas
        if len(pareto_journeys) < num_alternatives:
            pareto_journeys = self._add_diverse_alternatives(
                pareto_journeys, unique_journeys, num_alternatives
            )

        # Agregar opción de caminata directa si existe y no está ya representada
        if direct_walk_journeys:
            combined = direct_walk_journeys + pareto_journeys
            combined.sort()
            combined = self._filter_similar_journeys(combined)
            pareto_journeys = self._pareto_filter(combined)
            if len(pareto_journeys) < num_alternatives:
                pareto_journeys = self._add_diverse_alternatives(
                    pareto_journeys, combined, num_alternatives
                )

        return pareto_journeys[:num_alternatives]
    
    def _connection_scan(self,
                         origin_stop: str,
                         destination_stop: str,
                         start_time: datetime,
                         origin_coords: Tuple[float, float],
                         dest_coords: Tuple[float, float],
                         origin_walk_dist: float,
                         dest_walk_dist: float,
                         actual_departure: datetime,
                         num_alternatives: int = 3) -> List[Journey]:
        """
        Algoritmo Connection Scan principal.
        Encuentra la ruta óptima entre dos paradas usando GTFS y transferencias.
        """
        earliest_arrival = {origin_stop: start_time}
        in_connection = {}
        transfers_used = {origin_stop: 0}

        # (arrival_time, stop_id, current_route, num_transfers)
        queue = [(start_time, origin_stop, None, 0)]

        # Paradas ya liquidadas: no aportan mejoras si se vuelven a sacar
        settled: set = set()
        # Evitar explorar el mismo (parada, ruta) dos veces
        visited_route_at_stop: set = set()

        journeys_found = []
        time_horizon = start_time + timedelta(hours=3)

        while queue:
            arrival_time, current_stop, current_route, num_transfers = heapq.heappop(queue)

            # Ignorar entradas obsoletas de la cola
            if arrival_time > earliest_arrival.get(current_stop, arrival_time):
                continue
            settle_key = (current_stop, current_route)
            if settle_key in settled:
                continue
            settled.add(settle_key)

            # Horizonte temporal: no buscar más allá de 3 h desde la salida
            if arrival_time > time_horizon:
                continue

            if current_stop == destination_stop:
                journey = self._reconstruct_journey(
                    origin_stop, destination_stop,
                    in_connection, earliest_arrival,
                    origin_coords, dest_coords,
                    origin_walk_dist, dest_walk_dist,
                    actual_departure,
                )
                if journey:
                    journeys_found.append(journey)
                    if len(journeys_found) >= max(num_alternatives, 3):
                        break
                continue

            # ── Atajo de caminata al destino ─────────────────────────────────
            # Si desde la parada actual se puede caminar al destino en ≤ max_walking_km,
            # tratar esta parada como si fuera el destino y reconstruir el viaje.
            # Así se evita embarcar más buses innecesariamente.
            current_coords = self.gtfs.get_stop_coords(current_stop)
            if current_coords:
                cur_lon, cur_lat = current_coords
                dist_to_dest = self._haversine(
                    cur_lon, cur_lat,
                    dest_coords[1], dest_coords[0],
                )
                if dist_to_dest <= self.max_walking_km and current_stop != origin_stop:
                    # Registrar esta parada como destino virtual con caminata final
                    if current_stop not in in_connection:
                        pass  # aún no hay camino válido hasta aquí desde origin
                    else:
                        journey = self._reconstruct_journey(
                            origin_stop, current_stop,
                            in_connection, earliest_arrival,
                            origin_coords, dest_coords,
                            origin_walk_dist, dist_to_dest,
                            actual_departure,
                        )
                        if journey:
                            journeys_found.append(journey)
                            if len(journeys_found) >= max(num_alternatives, 3):
                                break
            # ─────────────────────────────────────────────────────────────────

            if num_transfers > self.max_transfers:
                continue

            for route_id in self._get_routes_at_stop(current_stop):
                key = (current_stop, route_id)
                if key in visited_route_at_stop:
                    continue
                visited_route_at_stop.add(key)

                needs_transfer = current_route is not None and current_route != route_id

                if needs_transfer:
                    if not self._is_transfer_viable(current_route, current_stop, route_id):
                        continue
                    # dep_time es una variable local para no mutar arrival_time del loop
                    dep_time = arrival_time + timedelta(seconds=_TRANSFER_PENALTY_SECONDS)
                else:
                    dep_time = arrival_time

                # Sucesores inmediatos en la ruta (uno por sentido cuando aplica);
                # Dijkstra propaga el resto.
                next_stops = self._get_next_stops_on_route(route_id, current_stop, dep_time)
                if not next_stops:
                    continue

                new_transfers = num_transfers + (1 if needs_transfer else 0)

                for next_stop, next_arrival_time in next_stops:
                    if next_stop not in earliest_arrival or next_arrival_time < earliest_arrival[next_stop]:
                        earliest_arrival[next_stop] = next_arrival_time
                        in_connection[next_stop] = (current_stop, route_id, dep_time, next_arrival_time)
                        transfers_used[next_stop] = new_transfers
                        heapq.heappush(queue, (next_arrival_time, next_stop, route_id, new_transfers))

        return journeys_found
    
    def _get_routes_at_stop(self, stop_id: str) -> List[str]:
        """Obtiene todas las rutas que pasan por una parada (O(1) con índice)."""
        return self._stop_to_routes.get(stop_id, [])
    
    def _is_transfer_viable(self, from_route: str, stop_id: str, to_route: str) -> bool:
        """Verifica si una transferencia es viable"""
        if not self.transfer_manager:
            # Sin transfer manager, permitir todas las transferencias
            return True
        
        # Obtener transferencias disponibles
        transfers = self.transfer_manager.get_transfers_from(from_route, stop_id)
        
        # Verificar si existe una transferencia viable a la ruta destino
        for transfer in transfers:
            if transfer.to_route_id == to_route and transfer.is_viable():
                return True
        
        return False
    
    def _get_next_stops_on_route(self, route_id: str, current_stop: str,
                                 current_time: datetime) -> List[Tuple[str, datetime]]:
        """
        Devuelve las paradas inmediatamente sucesivas en la ruta usando el
        grafo dirigido por trip (self.gtfs.graphs[route_id]). Esto preserva
        la dirección del recorrido — agarrar la sucesión por orden de
        `sequence` mezcla ida y vuelta y produce zigzag.

        Cada parada puede tener varios sucesores cuando varios trips
        (sentidos distintos) la atraviesan; Dijkstra deja al frente que
        gane el mejor.
        """
        graph = self.gtfs.graphs.get(route_id)
        node_map = self.gtfs._graph_node_maps.get(route_id)
        idx_to_node = self.gtfs._graph_idx_to_node.get(route_id)

        if graph is None or not node_map or current_stop not in node_map:
            return []

        src_idx = node_map[current_stop]
        successor_indices = graph.successor_indices(src_idx)
        if not successor_indices:
            return []

        route_stops = self.gtfs.route_stops.get(route_id, {})
        current_time_only = current_time.time()
        results: List[Tuple[str, datetime]] = []

        # arrival_times de cada parada vienen pre-ordenadas por GTFSData._build_route_index,
        # así que basta con buscar el primer tiempo >= current_time_only.
        for next_idx in successor_indices:
            next_stop_id = idx_to_node.get(next_idx)
            if next_stop_id is None:
                continue

            next_info = route_stops.get(next_stop_id, {})
            arrival_times = next_info.get("arrival_times") or ()

            next_arrival_dt: Optional[datetime] = None
            for t in arrival_times:
                if t >= current_time_only:
                    next_arrival_dt = datetime.combine(current_time.date(), t)
                    break

            if next_arrival_dt and next_arrival_dt > current_time:
                estimated_time = next_arrival_dt
            else:
                estimated_time = current_time + timedelta(minutes=2)

            results.append((next_stop_id, estimated_time))

        return results
    
    def _reconstruct_journey(self,
                             origin_stop: str,
                             destination_stop: str,
                             in_connection: dict,
                             earliest_arrival: dict,
                             origin_coords: Tuple[float, float],
                             dest_coords: Tuple[float, float],
                             origin_walk_dist: float,
                             dest_walk_dist: float,
                             actual_departure: datetime) -> Optional[Journey]:
        """
        Reconstruye el viaje desde la información de conexiones.
        """
        if destination_stop not in in_connection:
            return None
        
        segments = []
        current_stop = destination_stop
        num_transfers = 0
        
        # Reconstruir en reversa desde destino a origen
        path = []
        while current_stop != origin_stop:
            if current_stop not in in_connection:
                break
            
            from_stop, route_id, dep_time, arr_time = in_connection[current_stop]
            path.append((from_stop, current_stop, route_id, dep_time, arr_time))
            current_stop = from_stop
        
        if not path or current_stop != origin_stop:
            return None
        
        # Invertir el path (ahora va de origen a destino)
        path.reverse()
        
        # Segmento 1: Caminata inicial
        walk_time_sec = (origin_walk_dist / self.walking_speed) * 3600
        segments.append({
            'type': 'walk',
            'from': 'origin',
            'to': origin_stop,
            'from_latlon': [origin_coords[0], origin_coords[1]],
            'distance_km': origin_walk_dist,
            'duration': timedelta(seconds=walk_time_sec),
            'start_time': actual_departure,
            'end_time': actual_departure + timedelta(seconds=walk_time_sec)
        })
        
        # Segmentos de tránsito
        prev_route = None
        for from_stop, to_stop, route_id, dep_time, arr_time in path:
            if prev_route is not None and prev_route != route_id:
                num_transfers += 1
                segments.append({
                    'type': 'transfer',
                    'from_route': prev_route,
                    'to_route': route_id,
                    'at_stop': from_stop,
                    'duration': timedelta(seconds=_TRANSFER_PENALTY_SECONDS),
                })
            
            segments.append({
                'type': 'transit',
                'route_id': route_id,
                'from_stop': from_stop,
                'to_stop': to_stop,
                'departure_time': dep_time,
                'arrival_time': arr_time,
                'duration': arr_time - dep_time
            })
            
            prev_route = route_id
        
        # Segmento final: Caminata al destino
        final_walk_time_sec = (dest_walk_dist / self.walking_speed) * 3600
        last_segment_end = segments[-1]['arrival_time'] if segments[-1]['type'] == 'transit' else segments[-1]['end_time']

        segments.append({
            'type': 'walk',
            'from': destination_stop,
            'to': 'destination',
            'to_latlon': [dest_coords[0], dest_coords[1]],
            'distance_km': dest_walk_dist,
            'duration': timedelta(seconds=final_walk_time_sec),
            'start_time': last_segment_end,
            'end_time': last_segment_end + timedelta(seconds=final_walk_time_sec)
        })
        
        # Calcular totales
        total_duration = segments[-1]['end_time'] - segments[0]['start_time']
        total_walking = origin_walk_dist + dest_walk_dist
        
        return Journey(
            segments=segments,
            total_duration=total_duration,
            departure_time=actual_departure,
            arrival_time=segments[-1]['end_time'],
            number_of_transfers=num_transfers,
            total_walking_distance=total_walking
        )
    
    @staticmethod
    def _haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Distancia haversine en km."""
        rl1, rl2 = radians(lat1), radians(lat2)
        dl = radians(lat2 - lat1) * 0.5
        dl2 = radians(lon2 - lon1) * 0.5
        a = sin(dl) ** 2 + cos(rl1) * cos(rl2) * sin(dl2) ** 2
        return _EARTH_RADIUS_KM * 2.0 * asin(sqrt(a))

    @staticmethod
    def _filter_similar_journeys(journeys: List[Journey]) -> List[Journey]:
        """
        Filtra viajes con exactamente la misma secuencia de rutas de tránsito.
        Viajes de caminata pura se tratan como secuencia vacía única.
        """
        if not journeys:
            return []

        seen: set = set()
        unique: List[Journey] = []
        for journey in journeys:
            transit_seq = tuple(
                s["route_id"] for s in journey.segments if s["type"] == "transit"
            )
            key = transit_seq or ("__walk__",)
            if key not in seen:
                seen.add(key)
                unique.append(journey)

        return unique

    def _add_diverse_alternatives(
        self,
        pareto: List[Journey],
        candidates: List[Journey],
        target: int,
    ) -> List[Journey]:
        """
        Completa la lista de Pareto con viajes dominados que usan conjuntos de rutas
        distintos, para garantizar diversidad cuando el frente de Pareto es pequeño.
        """
        result = list(pareto)
        used_route_sets = {
            frozenset(s["route_id"] for s in j.segments if s["type"] == "transit")
            for j in result
        }
        for journey in candidates:
            if len(result) >= target:
                break
            route_set = frozenset(
                s["route_id"] for s in journey.segments if s["type"] == "transit"
            )
            if route_set not in used_route_sets:
                result.append(journey)
                used_route_sets.add(route_set)
        result.sort()
        return result

    @staticmethod
    def _pareto_filter(journeys: List[Journey]) -> List[Journey]:
        """
        Retorna el frente de Pareto de los viajes según duración y transferencias.

        Un viaje A domina a B si A es al menos tan bueno en ambas dimensiones
        (duración, número de transferencias) y estrictamente mejor en al menos una.
        Solo los no dominados se incluyen en la salida, ordenados de menor a mayor
        duración.

        Implementación O(n log n): tras ordenar por (duración, transferencias),
        un viaje es dominado si y sólo si algún viaje anterior tiene menos o igual
        transferencias. Con empate exacto en ambas métricas, se conserva el primero.
        """
        if not journeys:
            return []

        ordered = sorted(
            journeys,
            key=lambda j: (j.total_duration, j.number_of_transfers),
        )

        pareto: List[Journey] = []
        best_transfers = -1  # cualquier conteo real es >= 0
        for journey in ordered:
            transfers = journey.number_of_transfers
            if best_transfers == -1 or transfers < best_transfers:
                pareto.append(journey)
                best_transfers = transfers

        return pareto


def create_csa_planner(gtfs_data, transfer_manager=None,
                      max_walking_km: float = 0.8,
                      walking_speed_kmh: float = 5.0,
                      max_transfers: int = 3):
    """
    Factory function para crear una instancia de ConnectionScanAlgorithm.
    
    Args:
        gtfs_data: Instancia de GTFSData
        transfer_manager: TransferManager opcional
        max_walking_km: Distancia máxima de caminata
        walking_speed_kmh: Velocidad de caminata
        max_transfers: Máximo número de transferencias
    
    Returns:
        ConnectionScanAlgorithm configurado
    """
    return ConnectionScanAlgorithm(
        gtfs_data,
        transfer_manager,
        max_walking_km,
        walking_speed_kmh,
        max_transfers
    )
