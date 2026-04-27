"""
Connection Scan Algorithm con soporte para transbordos
Implementación optimizada para planificación de viajes multimodales
"""

from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from dataclasses import dataclass
import bisect
import heapq


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
                 max_walking_distance_km: float = 1.0,
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
            from collections import defaultdict
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
        
        # Paso 1: Encontrar paradas cercanas al origen
        origin_stops = self.gtfs.get_nearby_stops(
            origin_coords, 
            margin_km=self.max_walking_km
        )
        
        if not origin_stops:
            return []
        
        # Paso 2: Encontrar paradas cercanas al destino
        destination_stops = self.gtfs.get_nearby_stops(
            destination_coords,
            margin_km=self.max_walking_km
        )
        
        if not destination_stops:
            return []
        
        # Paso 3: Ejecutar CSA para cada combinación de paradas
        all_journeys = []

        for origin_stop, origin_dist in origin_stops:
            origin_walk_time = (origin_dist / self.walking_speed) * 3600
            arrival_at_origin_stop = departure_time + timedelta(seconds=origin_walk_time)

            for dest_stop, dest_dist in destination_stops:
                dest_walk_time = (dest_dist / self.walking_speed) * 3600

                journeys = self._connection_scan(
                    origin_stop,
                    dest_stop,
                    arrival_at_origin_stop,
                    origin_coords,
                    destination_coords,
                    origin_dist,
                    dest_dist,
                    departure_time,
                )

                all_journeys.extend(journeys)
        
        # Paso 4: Ordenar y retornar las mejores rutas
        if not all_journeys:
            return []

        # Ordenar por duración total, luego por número de transferencias
        all_journeys.sort()

        # Filtrar rutas muy similares (mismas paradas, misma ruta)
        unique_journeys = self._filter_similar_journeys(all_journeys)

        # Aplicar filtro Pareto (tiempo vs transferencias)
        pareto_journeys = self._pareto_filter(unique_journeys)

        return pareto_journeys[:num_alternatives]
    
    def _connection_scan(self,
                         origin_stop: str,
                         destination_stop: str,
                         start_time: datetime,
                         origin_coords: Tuple[float, float],
                         dest_coords: Tuple[float, float],
                         origin_walk_dist: float,
                         dest_walk_dist: float,
                         actual_departure: datetime) -> List[Journey]:
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
                    if len(journeys_found) >= 3:
                        break
                continue

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
                    dep_time = arrival_time + timedelta(seconds=120)
                else:
                    dep_time = arrival_time

                # Solo el siguiente stop inmediato en la ruta; Dijkstra propaga el resto
                next_stop_pair = self._get_next_stop_on_route(route_id, current_stop, dep_time)
                if next_stop_pair is None:
                    continue

                next_stop, next_arrival_time = next_stop_pair
                new_transfers = num_transfers + (1 if needs_transfer else 0)

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
    
    def _get_next_stop_on_route(self, route_id: str, current_stop: str,
                                current_time: datetime) -> Optional[Tuple[str, datetime]]:
        """
        Devuelve SOLO la parada inmediatamente siguiente en la ruta.
        Dijkstra propaga de forma natural de parada en parada, por lo que
        no es necesario expandir todas las paradas futuras de golpe.
        """
        route_stops = self.gtfs.route_stops.get(route_id)
        if not route_stops or current_stop not in route_stops:
            return None

        current_info = route_stops[current_stop]
        current_sequence = current_info["sequence"]
        has_arrival_times = bool(current_info.get("arrival_times"))
        current_time_only = current_time.time()

        sorted_stops = getattr(self.gtfs, "_sorted_route_stops", {}).get(route_id, [])
        if not sorted_stops:
            return None

        sequences = [info["sequence"] for _, info in sorted_stops]
        start_idx = bisect.bisect_right(sequences, current_sequence)

        if start_idx >= len(sorted_stops):
            return None

        next_stop_id, next_info = sorted_stops[start_idx]
        sequence_diff = next_info["sequence"] - current_sequence

        if has_arrival_times and next_info.get("arrival_times"):
            next_arrival = None
            for t in sorted(next_info["arrival_times"]):
                if t >= current_time_only:
                    next_arrival = datetime.combine(current_time.date(), t)
                    break
            estimated_time = (
                next_arrival
                if next_arrival and next_arrival > current_time
                else current_time + timedelta(minutes=2 * sequence_diff)
            )
        else:
            estimated_time = current_time + timedelta(minutes=2 * sequence_diff)

        return next_stop_id, estimated_time
    
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
                    'duration': timedelta(minutes=2)
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
    
    def _filter_similar_journeys(self, journeys: List[Journey]) -> List[Journey]:
        """
        Filtra viajes con exactamente las mismas rutas de tránsito.
        """
        if not journeys:
            return []

        unique = [journeys[0]]
        for journey in journeys[1:]:
            journey_routes = [s['route_id'] for s in journey.segments if s['type'] == 'transit']
            if not any(
                journey_routes == [s['route_id'] for s in ex.segments if s['type'] == 'transit']
                for ex in unique
            ):
                unique.append(journey)

        return unique

    def _pareto_filter(self, journeys: List[Journey]) -> List[Journey]:
        """
        Retorna el frente de Pareto de los viajes según duración y transferencias.

        Un viaje A domina a B si A es al menos tan bueno en ambas dimensiones
        (duración, número de transferencias) y estrictamente mejor en al menos una.
        Solo los no dominados se incluyen en la salida, ordenados de menor a mayor
        duración.
        """
        if not journeys:
            return []

        pareto: List[Journey] = []
        for candidate in journeys:
            dominated = False
            for other in journeys:
                if other is candidate:
                    continue
                # other domina a candidate si es <= en ambas métricas y < en al menos una
                if (other.total_duration <= candidate.total_duration and
                        other.number_of_transfers <= candidate.number_of_transfers and
                        (other.total_duration < candidate.total_duration or
                         other.number_of_transfers < candidate.number_of_transfers)):
                    dominated = True
                    break
            if not dominated:
                pareto.append(candidate)

        pareto.sort()
        return pareto


def create_csa_planner(gtfs_data, transfer_manager=None,
                      max_walking_km: float = 1.0,
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
