"""
Connection Scan Algorithm con soporte para transbordos
Implementación optimizada para planificación de viajes multimodales
"""

from datetime import datetime, timedelta, time, date
from typing import List, Tuple, Optional, Dict, Set
from dataclasses import dataclass
import heapq


@dataclass
class Connection:
    """
    Representa una conexión de transporte público.
    Una conexión es un viaje directo entre dos paradas en una ruta específica.
    """
    route_id: str
    from_stop_id: str
    to_stop_id: str
    departure_time: datetime
    arrival_time: datetime
    trip_id: Optional[str] = None
    
    def __lt__(self, other):
        """Para ordenamiento en priority queue"""
        return self.departure_time < other.departure_time
    
    @property
    def travel_time(self) -> timedelta:
        """Tiempo de viaje de esta conexión"""
        return self.arrival_time - self.departure_time


@dataclass
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
        
        # Cache para conexiones
        self._connections_cache = None
        self._connections_by_stop = None
    
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
        print(f"\n🔍 Buscando rutas desde {origin_coords} a {destination_coords}")
        print(f"   Salida: {departure_time.strftime('%H:%M')}")
        
        # Paso 1: Encontrar paradas cercanas al origen
        origin_stops = self.gtfs.get_nearby_stops(
            origin_coords, 
            margin_km=self.max_walking_km
        )
        
        if not origin_stops:
            print("❌ No hay paradas cerca del origen")
            return []
        
        print(f"   Paradas origen: {len(origin_stops)} encontradas")
        
        # Paso 2: Encontrar paradas cercanas al destino
        destination_stops = self.gtfs.get_nearby_stops(
            destination_coords,
            margin_km=self.max_walking_km
        )
        
        if not destination_stops:
            print("❌ No hay paradas cerca del destino")
            return []
        
        print(f"   Paradas destino: {len(destination_stops)} encontradas")
        
        # Paso 3: Ejecutar CSA para cada combinación de paradas
        all_journeys = []
        
        for origin_stop, origin_dist, origin_walk_time in origin_stops:
            for dest_stop, dest_dist, dest_walk_time in destination_stops:
                
                # Calcular tiempo de llegada a la parada de origen
                arrival_at_origin_stop = departure_time + timedelta(seconds=origin_walk_time)
                
                # Buscar rutas desde esta parada de origen a destino
                journeys = self._connection_scan(
                    origin_stop,
                    dest_stop,
                    arrival_at_origin_stop,
                    origin_coords,
                    destination_coords,
                    origin_dist,
                    dest_dist,
                    departure_time
                )
                
                all_journeys.extend(journeys)
        
        # Paso 4: Ordenar y retornar las mejores rutas
        if not all_journeys:
            print("❌ No se encontraron rutas válidas")
            return []
        
        # Ordenar por duración total, luego por número de transferencias
        all_journeys.sort()
        
        # Filtrar rutas muy similares (mismas paradas, misma ruta)
        unique_journeys = self._filter_similar_journeys(all_journeys)
        
        print(f"✅ {len(unique_journeys)} rutas únicas encontradas")
        
        return unique_journeys[:num_alternatives]
    
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
        # Estructuras de datos para el algoritmo
        earliest_arrival = {origin_stop: start_time}  # Tiempo de llegada más temprano a cada parada
        in_connection = {}  # Conexión entrante óptima para cada parada
        transfers_used = {origin_stop: 0}  # Número de transferencias usadas
        
        # Priority queue de paradas a explorar
        queue = [(start_time, origin_stop, None, 0)]  # (arrival_time, stop_id, from_route, num_transfers)
        
        visited_routes = set()
        journeys_found = []
        
        while queue:
            arrival_time, current_stop, current_route, num_transfers = heapq.heappop(queue)
            
            # Si ya llegamos al destino, construir el viaje
            if current_stop == destination_stop:
                journey = self._reconstruct_journey(
                    origin_stop, destination_stop,
                    in_connection, earliest_arrival,
                    origin_coords, dest_coords,
                    origin_walk_dist, dest_walk_dist,
                    actual_departure
                )
                if journey:
                    journeys_found.append(journey)
                    # Continuar buscando rutas alternativas
                    if len(journeys_found) >= 3:
                        break
                continue
            
            # No superar el máximo de transferencias
            if num_transfers > self.max_transfers:
                continue
            
            # Explorar rutas que pasan por esta parada
            routes_at_stop = self._get_routes_at_stop(current_stop)
            
            for route_id in routes_at_stop:
                if (current_stop, route_id) in visited_routes:
                    continue
                visited_routes.add((current_stop, route_id))
                
                # Si es transferencia (cambio de ruta), verificar viabilidad
                needs_transfer = current_route is not None and current_route != route_id
                
                if needs_transfer:
                    # Verificar si la transferencia es viable
                    if not self._is_transfer_viable(current_route, current_stop, route_id):
                        continue
                    
                    # Agregar tiempo de transferencia
                    transfer_time = 120  # 2 minutos mínimo
                    arrival_time = arrival_time + timedelta(seconds=transfer_time)
                
                # Obtener siguientes paradas en esta ruta
                next_stops = self._get_next_stops_on_route(route_id, current_stop, arrival_time)
                
                for next_stop, next_arrival_time in next_stops:
                    # Actualizar si encontramos un tiempo de llegada mejor
                    if next_stop not in earliest_arrival or next_arrival_time < earliest_arrival[next_stop]:
                        earliest_arrival[next_stop] = next_arrival_time
                        in_connection[next_stop] = (current_stop, route_id, arrival_time, next_arrival_time)
                        transfers_used[next_stop] = num_transfers + (1 if needs_transfer else 0)
                        
                        heapq.heappush(queue, (
                            next_arrival_time,
                            next_stop,
                            route_id,
                            transfers_used[next_stop]
                        ))
        
        return journeys_found
    
    def _get_routes_at_stop(self, stop_id: str) -> List[str]:
        """Obtiene todas las rutas que pasan por una parada"""
        routes = []
        for route_id, stops_dict in self.gtfs.route_stops.items():
            if stop_id in stops_dict:
                routes.append(route_id)
        return routes
    
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
        Obtiene las siguientes paradas en una ruta después de la parada actual.
        Retorna: [(stop_id, estimated_arrival_time), ...]
        """
        if route_id not in self.gtfs.route_stops:
            return []
        
        route_stops = self.gtfs.route_stops[route_id]
        
        if current_stop not in route_stops:
            return []
        
        current_sequence = route_stops[current_stop]['sequence']
        
        # Encontrar paradas siguientes en la secuencia
        next_stops = []
        for stop_id, stop_info in route_stops.items():
            if stop_info['sequence'] > current_sequence:
                # Estimar tiempo de llegada (simplificado)
                # En una implementación completa, usar horarios reales del GTFS
                sequence_diff = stop_info['sequence'] - current_sequence
                estimated_time = current_time + timedelta(minutes=2 * sequence_diff)
                
                next_stops.append((stop_id, estimated_time))
        
        return next_stops
    
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
        Filtra viajes muy similares para retornar solo opciones distintas.
        """
        if not journeys:
            return []
        
        unique = [journeys[0]]
        
        for journey in journeys[1:]:
            is_different = True
            
            for existing in unique:
                # Comparar rutas usadas
                journey_routes = [seg['route_id'] for seg in journey.segments if seg['type'] == 'transit']
                existing_routes = [seg['route_id'] for seg in existing.segments if seg['type'] == 'transit']
                
                if journey_routes == existing_routes:
                    is_different = False
                    break
            
            if is_different:
                unique.append(journey)
        
        return unique


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
