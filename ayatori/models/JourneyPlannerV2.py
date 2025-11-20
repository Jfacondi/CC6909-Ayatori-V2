"""
JourneyPlanner Mejorado - Versión 2.0
Incluye Connection Scan Algorithm y tiempos dinámicos
"""

from datetime import datetime, timedelta
from typing import List, Tuple, Optional
from dataclasses import dataclass


class JourneyLeg:
    """Representa un segmento de un viaje (caminata, tránsito, o transbordo)"""
    
    def __init__(self, leg_type: str, start_time: datetime, end_time: datetime,
                 distance: float = 0, route_id: str = None,
                 from_stop: str = None, to_stop: str = None,
                 transfer_from: str = None, transfer_to: str = None):
        """
        Crea un segmento de viaje.
        
        Args:
            leg_type: 'walk', 'transit', o 'transfer'
            start_time: Hora de inicio
            end_time: Hora de fin
            distance: Distancia en km (para caminata)
            route_id: ID de ruta (para tránsito)
            from_stop: Parada de origen (para tránsito)
            to_stop: Parada de destino (para tránsito)
            transfer_from: Ruta de origen (para transbordo)
            transfer_to: Ruta de destino (para transbordo)
        """
        self.leg_type = leg_type
        self.start_time = start_time
        self.end_time = end_time
        self.duration = end_time - start_time
        
        # Atributos específicos por tipo
        self.distance = distance  # Para caminata
        self.route_id = route_id  # Para tránsito
        self.from_stop = from_stop  # Para tránsito
        self.to_stop = to_stop  # Para tránsito
        self.transfer_from = transfer_from  # Para transbordo
        self.transfer_to = transfer_to  # Para transbordo
    
    def __repr__(self):
        if self.leg_type == 'walk':
            mins = self.duration.total_seconds() / 60
            return f"Walk({self.distance:.2f}km, {mins:.1f}min)"
        elif self.leg_type == 'transit':
            mins = self.duration.total_seconds() / 60
            return f"Transit(Route {self.route_id}, {self.from_stop}→{self.to_stop}, {mins:.1f}min)"
        elif self.leg_type == 'transfer':
            mins = self.duration.total_seconds() / 60
            return f"Transfer({self.transfer_from}→{self.transfer_to}, {mins:.1f}min)"
        return f"Leg({self.leg_type})"


class Journey:
    """Representa un viaje completo con múltiples segmentos"""
    
    def __init__(self, origin_coords: Tuple[float, float], 
                 destination_coords: Tuple[float, float]):
        """
        Crea un nuevo viaje.
        
        Args:
            origin_coords: Coordenadas de origen (lat, lon)
            destination_coords: Coordenadas de destino (lat, lon)
        """
        self.origin_coords = origin_coords
        self.destination_coords = destination_coords
        self.legs: List[JourneyLeg] = []
    
    def add_leg(self, leg: JourneyLeg):
        """Añade un segmento al viaje"""
        self.legs.append(leg)
    
    @property
    def total_duration(self) -> timedelta:
        """Duración total del viaje"""
        if not self.legs:
            return timedelta(0)
        return self.legs[-1].end_time - self.legs[0].start_time
    
    @property
    def total_walking_distance(self) -> float:
        """Distancia total caminada en km"""
        return sum(leg.distance for leg in self.legs if leg.leg_type == 'walk')
    
    @property
    def number_of_transfers(self) -> int:
        """Número de transbordos"""
        return sum(1 for leg in self.legs if leg.leg_type == 'transfer')
    
    def get_departure_time(self) -> Optional[datetime]:
        """Hora de salida del viaje"""
        return self.legs[0].start_time if self.legs else None
    
    def get_arrival_time(self) -> Optional[datetime]:
        """Hora de llegada del viaje"""
        return self.legs[-1].end_time if self.legs else None
    
    def __repr__(self):
        if not self.legs:
            return "Journey(empty)"
        
        duration_mins = self.total_duration.total_seconds() / 60
        dep = self.get_departure_time().strftime('%H:%M')
        arr = self.get_arrival_time().strftime('%H:%M')
        
        return (f"Journey({dep}→{arr}, {duration_mins:.0f}min, "
                f"{self.number_of_transfers} transfers, {self.total_walking_distance:.2f}km walk)")


class JourneyPlannerV2:
    """
    Planificador de viajes mejorado con Connection Scan Algorithm.
    Soporta múltiples transferencias y tiempos dinámicos.
    """
    
    def __init__(self, gtfs_data, max_walking_km: float = 1.0,
                 walking_speed_kmh: float = 5.0):
        """
        Inicializa el planificador.
        
        Args:
            gtfs_data: Instancia de GTFSData
            max_walking_km: Distancia máxima de caminata
            walking_speed_kmh: Velocidad de caminata
        """
        self.gtfs = gtfs_data
        self.max_walking_distance = max_walking_km
        self.walking_speed = walking_speed_kmh
    
    def find_nearby_origin_stops(self, origin_coords: Tuple[float, float],
                                 max_stops: int = 5) -> List[Tuple[str, float, float]]:
        """
        Encuentra paradas cercanas al origen ordenadas por distancia.
        
        Args:
            origin_coords: Coordenadas (lat, lon)
            max_stops: Número máximo de paradas
            
        Returns:
            Lista de tuplas (stop_id, distance_km, walking_time_seconds)
        """
        nearby = self.gtfs.get_nearby_stops(
            origin_coords,
            margin_km=self.max_walking_distance,
            max_stops=max_stops
        )
        
        result = []
        for stop_id, distance in nearby:
            walk_time = self.gtfs.walking_travel_time(
                self.gtfs.stop_coords.get(stop_id, (0, 0)),
                origin_coords,
                self.walking_speed
            )
            result.append((stop_id, distance, walk_time))
        
        return result
    
    def find_nearby_destination_stops(self, destination_coords: Tuple[float, float],
                                     max_stops: int = 5) -> List[Tuple[str, float, float]]:
        """
        Encuentra paradas cercanas al destino.
        
        Args:
            destination_coords: Coordenadas de destino (lat, lon)
            max_stops: Número máximo de paradas a retornar
            
        Returns:
            Lista de tuplas (stop_id, distance_km, walking_time_seconds)
        """
        return self.find_nearby_origin_stops(destination_coords, max_stops)
    
    def plan_journey(self, 
                     origin_coords: Tuple[float, float],
                     destination_coords: Tuple[float, float],
                     departure_time: datetime,
                     max_transfers: int = 2,
                     use_csa: bool = True) -> Optional[Journey]:
        """
        Planifica un viaje completo desde origen a destino.
        
        Args:
            origin_coords: Tupla (lat, lon) del origen
            destination_coords: Tupla (lat, lon) del destino
            departure_time: Hora de salida deseada
            max_transfers: Número máximo de transferencias permitidas
            use_csa: Si True, usa Connection Scan Algorithm; si False, usa método simplificado
            
        Returns:
            Journey con el viaje planificado, o None si no se encuentra ruta
        """
        print(f"\n🗺️  Planificando viaje (v2.0)...")
        print(f"   Origen: {origin_coords}")
        print(f"   Destino: {destination_coords}")
        print(f"   Salida: {departure_time}")
        print(f"   Método: {'CSA (óptimo)' if use_csa else 'Simplificado'}")
        
        if use_csa:
            # Usar Connection Scan Algorithm con soporte para múltiples transferencias
            try:
                from .ConnectionScanAlgorithm import create_csa_planner
                
                # Crear planificador CSA
                csa = create_csa_planner(
                    self.gtfs,
                    transfer_manager=getattr(self.gtfs, 'transfer_manager', None),
                    max_walking_km=self.max_walking_distance,
                    walking_speed_kmh=self.walking_speed,
                    max_transfers=max_transfers
                )
                
                # Buscar rutas
                csa_journeys = csa.find_journey(
                    origin_coords,
                    destination_coords,
                    departure_time,
                    num_alternatives=3
                )
                
                if csa_journeys:
                    # Convertir el primer Journey de CSA a nuestro formato
                    return self._convert_csa_journey_to_legacy(csa_journeys[0])
                else:
                    print("⚠️  CSA no encontró rutas, usando método simplificado...")
                    return self._plan_journey_simple(origin_coords, destination_coords, departure_time)
                    
            except Exception as e:
                print(f"⚠️  Error con CSA: {e}")
                import traceback
                traceback.print_exc()
                print("   Usando método simplificado...")
                return self._plan_journey_simple(origin_coords, destination_coords, departure_time)
        else:
            return self._plan_journey_simple(origin_coords, destination_coords, departure_time)
    
    def _convert_csa_journey_to_legacy(self, csa_journey) -> Journey:
        """
        Convierte un Journey de CSA al formato de JourneyPlanner.
        """
        from .ConnectionScanAlgorithm import Journey as CSAJourney
        
        if not isinstance(csa_journey, CSAJourney):
            return None
        
        # Extraer coordenadas
        origin_coords = self.gtfs.stop_coords.get(
            csa_journey.segments[0].get('to', ''),
            (0, 0)
        )
        dest_coords = self.gtfs.stop_coords.get(
            csa_journey.segments[-1].get('from', ''),
            (0, 0)
        )
        
        journey = Journey(origin_coords, dest_coords)
        
        # Convertir cada segmento
        for segment in csa_journey.segments:
            if segment['type'] == 'walk':
                leg = JourneyLeg(
                    leg_type='walk',
                    start_time=segment['start_time'],
                    end_time=segment['end_time'],
                    distance=segment['distance_km']
                )
            elif segment['type'] == 'transit':
                leg = JourneyLeg(
                    leg_type='transit',
                    start_time=segment['departure_time'],
                    end_time=segment['arrival_time'],
                    route_id=segment['route_id'],
                    from_stop=segment['from_stop'],
                    to_stop=segment['to_stop']
                )
            elif segment['type'] == 'transfer':
                start = segment.get('start_time')
                if not start and journey.legs:
                    start = journey.legs[-1].end_time
                end = start + segment['duration'] if start else datetime.now()
                
                leg = JourneyLeg(
                    leg_type='transfer',
                    start_time=start,
                    end_time=end,
                    transfer_from=segment['from_route'],
                    transfer_to=segment['to_route']
                )
            else:
                continue
            
            journey.add_leg(leg)
        
        return journey
    
    def _plan_journey_simple(self,
                            origin_coords: Tuple[float, float],
                            destination_coords: Tuple[float, float],
                            departure_time: datetime) -> Optional[Journey]:
        """
        Método simplificado de planificación (sin CSA).
        Encuentra rutas directas sin transferencias complejas.
        """
        # Paso 1: Encontrar paradas cercanas al origen
        print(f"\n📍 Buscando paradas cerca del origen...")
        origin_stops = self.find_nearby_origin_stops(origin_coords)
        
        if not origin_stops:
            print(f"❌ No se encontraron paradas cerca del origen")
            return None
        
        print(f"   ✅ {len(origin_stops)} paradas encontradas")
        
        # Paso 2: Encontrar paradas cercanas al destino
        print(f"\n📍 Buscando paradas cerca del destino...")
        destination_stops = self.find_nearby_destination_stops(destination_coords)
        
        if not destination_stops:
            print(f"❌ No se encontraron paradas cerca del destino")
            return None
        
        print(f"   ✅ {len(destination_stops)} paradas encontradas")
        
        # Paso 3: Crear objeto Journey
        journey = Journey(origin_coords, destination_coords)
        
        # Usar la primera parada como origen (más cercana)
        best_origin_stop, origin_distance, origin_walk_time = origin_stops[0]
        
        # Crear segmento de caminata inicial
        walk_start = departure_time
        walk_end = walk_start + timedelta(seconds=origin_walk_time)
        
        initial_walk = JourneyLeg(
            leg_type='walk',
            start_time=walk_start,
            end_time=walk_end,
            distance=origin_distance
        )
        journey.add_leg(initial_walk)
        
        # Paso 4: Encontrar ruta de transporte público
        routes_at_origin = self._find_routes_at_stop(best_origin_stop)
        
        if not routes_at_origin:
            print(f"⚠️  No se encontraron rutas en parada {best_origin_stop}")
            return None
        
        # Paso 5: Para cada ruta, verificar si llega cerca del destino
        best_route = None
        best_destination_stop = None
        min_total_distance = float('inf')
        
        for route_id in routes_at_origin:
            if route_id not in self.gtfs.route_stops:
                continue
                
            route_stops = self.gtfs.route_stops[route_id]
            
            for stop_id in route_stops.keys():
                for dest_stop_id, dest_distance, _ in destination_stops:
                    if stop_id == dest_stop_id:
                        total_dist = origin_distance + dest_distance
                        if total_dist < min_total_distance:
                            min_total_distance = total_dist
                            best_route = route_id
                            best_destination_stop = stop_id
        
        if not best_route:
            print(f"⚠️  No se encontró ruta directa desde {best_origin_stop}")
            return None
        
        # Paso 6: Calcular tiempo de tránsito real usando horarios GTFS
        transit_start = walk_end
        transit_duration = self._estimate_transit_time(
            best_route, 
            best_origin_stop, 
            best_destination_stop,
            transit_start
        )
        transit_end = transit_start + transit_duration
        
        transit_leg = JourneyLeg(
            leg_type='transit',
            start_time=transit_start,
            end_time=transit_end,
            route_id=best_route,
            from_stop=best_origin_stop,
            to_stop=best_destination_stop
        )
        journey.add_leg(transit_leg)
        
        # Paso 7: Caminata final al destino
        dest_distance = next(
            (d for s, d, _ in destination_stops if s == best_destination_stop),
            0.5
        )
        dest_walk_time = (dest_distance / self.walking_speed) * 3600
        
        final_walk_start = transit_end
        final_walk_end = final_walk_start + timedelta(seconds=dest_walk_time)
        
        final_walk = JourneyLeg(
            leg_type='walk',
            start_time=final_walk_start,
            end_time=final_walk_end,
            distance=dest_distance
        )
        journey.add_leg(final_walk)
        
        return journey
    
    def _estimate_transit_time(self, route_id: str, from_stop: str, 
                               to_stop: str, departure_time: datetime) -> timedelta:
        """
        Estima el tiempo de tránsito entre dos paradas usando datos GTFS.
        
        Args:
            route_id: ID de la ruta
            from_stop: Parada de origen
            to_stop: Parada de destino
            departure_time: Hora de salida
            
        Returns:
            timedelta con el tiempo estimado de viaje
        """
        try:
            # Intentar obtener tiempos reales de GTFS
            if hasattr(self.gtfs, 'get_arrival_times'):
                departure_date = departure_time.date()
                
                # Obtener horarios de llegada a la parada de origen
                arrival_info = self.gtfs.get_arrival_times(
                    route_id, from_stop, departure_date
                )
                
                if arrival_info and len(arrival_info) > 1:
                    orientation, arrival_times = arrival_info
                    
                    # Encontrar el próximo bus después de departure_time
                    departure_time_only = departure_time.time()
                    next_buses = self.gtfs.get_time_until_next_bus(
                        arrival_times, departure_time_only, departure_date
                    )
                    
                    if next_buses:
                        # Usar tiempo de espera + tiempo de viaje estimado
                        wait_minutes, wait_seconds = next_buses[0]
                        wait_time = timedelta(minutes=wait_minutes, seconds=wait_seconds)
                        
                        # Estimar tiempo de viaje entre paradas
                        route_stops = self.gtfs.route_stops[route_id]
                        from_seq = route_stops[from_stop]['sequence']
                        to_seq = route_stops[to_stop]['sequence']
                        
                        stops_diff = abs(to_seq - from_seq)
                        travel_time = timedelta(minutes=2 * stops_diff)
                        
                        return wait_time + travel_time
            
            # Fallback: estimación simple basada en distancia de paradas
            if route_id in self.gtfs.route_stops:
                route_stops = self.gtfs.route_stops[route_id]
                if from_stop in route_stops and to_stop in route_stops:
                    from_seq = route_stops[from_stop]['sequence']
                    to_seq = route_stops[to_stop]['sequence']
                    stops_diff = abs(to_seq - from_seq)
                    return timedelta(minutes=3 * stops_diff)
            
        except Exception as e:
            print(f"   ⚠️  Error estimando tiempo: {e}")
        
        # Fallback final: 30 minutos
        return timedelta(minutes=30)
    
    def _find_routes_at_stop(self, stop_id: str) -> List[str]:
        """
        Encuentra todas las rutas que pasan por una parada.
        
        Args:
            stop_id: ID de la parada
            
        Returns:
            Lista de route_ids que pasan por la parada
        """
        routes = []
        for route_id, stops_dict in self.gtfs.route_stops.items():
            if stop_id in stops_dict:
                routes.append(route_id)
        return routes


# Función de conveniencia
def create_journey_planner_v2(gtfs_data, max_walking_km: float = 1.0,
                             walking_speed_kmh: float = 5.0) -> JourneyPlannerV2:
    """
    Crea una instancia del planificador de viajes v2.0.
    
    Args:
        gtfs_data: Instancia de GTFSData
        max_walking_km: Distancia máxima de caminata en km
        walking_speed_kmh: Velocidad de caminata en km/h
        
    Returns:
        JourneyPlannerV2 configurado
    """
    return JourneyPlannerV2(gtfs_data, max_walking_km, walking_speed_kmh)
