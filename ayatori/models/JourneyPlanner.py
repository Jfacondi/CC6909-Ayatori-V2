#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Journey Planner - Planificador de Viajes con Transporte Público

Este módulo implementa un planificador de viajes que:
- Integra caminata desde origen a parada inicial
- Calcula rutas en transporte público usando GTFS
- Integra transbordos entre rutas
- Calcula caminata desde parada final a destino
"""

from datetime import datetime, timedelta
from typing import List, Tuple, Optional, Dict
import heapq


class JourneyLeg:
    """Representa un segmento del viaje"""
    
    def __init__(self, leg_type: str, start_time: datetime, end_time: datetime, **kwargs):
        """
        Inicializa un segmento del viaje.
        
        Args:
            leg_type: Tipo de segmento ('walk', 'transit', 'transfer')
            start_time: Hora de inicio del segmento
            end_time: Hora de fin del segmento
            **kwargs: Parámetros adicionales según el tipo
        """
        self.leg_type = leg_type
        self.start_time = start_time
        self.end_time = end_time
        self.duration = (end_time - start_time).total_seconds()
        
        # Para segmentos de caminata
        self.walking_distance = kwargs.get('distance', 0)
        
        # Para segmentos de tránsito
        self.route_id = kwargs.get('route_id')
        self.from_stop = kwargs.get('from_stop')
        self.to_stop = kwargs.get('to_stop')
        
        # Para transferencias
        self.transfer_from = kwargs.get('transfer_from')
        self.transfer_to = kwargs.get('transfer_to')
    
    def __repr__(self):
        if self.leg_type == 'walk':
            return f"Walk({self.walking_distance:.2f}km, {self.duration/60:.1f}min)"
        elif self.leg_type == 'transit':
            return f"Transit(Route {self.route_id}, {self.from_stop}→{self.to_stop}, {self.duration/60:.1f}min)"
        elif self.leg_type == 'transfer':
            return f"Transfer({self.transfer_from}→{self.transfer_to}, {self.duration/60:.1f}min)"
        return f"Leg({self.leg_type})"


class Journey:
    """Representa un viaje completo con todos sus segmentos"""
    
    def __init__(self, origin_coords: Tuple[float, float], 
                 destination_coords: Tuple[float, float]):
        """
        Inicializa un viaje.
        
        Args:
            origin_coords: Coordenadas de origen (lat, lon)
            destination_coords: Coordenadas de destino (lat, lon)
        """
        self.origin_coords = origin_coords
        self.destination_coords = destination_coords
        self.legs: List[JourneyLeg] = []
        self.total_duration = timedelta()
        self.total_walking_distance = 0
        self.number_of_transfers = 0
    
    def add_leg(self, leg: JourneyLeg):
        """Agrega un segmento al viaje"""
        self.legs.append(leg)
        self.total_duration += timedelta(seconds=leg.duration)
        
        if leg.leg_type == 'walk':
            self.total_walking_distance += leg.walking_distance
        elif leg.leg_type == 'transfer':
            self.number_of_transfers += 1
    
    def get_departure_time(self) -> Optional[datetime]:
        """Retorna la hora de salida del viaje"""
        return self.legs[0].start_time if self.legs else None
    
    def get_arrival_time(self) -> Optional[datetime]:
        """Retorna la hora de llegada del viaje"""
        return self.legs[-1].end_time if self.legs else None
    
    def __repr__(self):
        return (f"Journey({len(self.legs)} legs, "
                f"{self.total_duration/60:.1f}min, "
                f"{self.number_of_transfers} transfers, "
                f"{self.total_walking_distance:.2f}km walk)")


class JourneyPlanner:
    """
    Planificador de viajes multimodal.
    
    Integra:
    - Caminata desde origen a paradas cercanas
    - Rutas de transporte público (GTFS)
    - Transbordos entre rutas
    - Caminata desde parada final a destino
    """
    
    def __init__(self, gtfs_data, max_walking_distance_km: float = 1.0,
                 walking_speed_kmh: float = 5.0):
        """
        Inicializa el planificador de viajes.
        
        Args:
            gtfs_data: Instancia de GTFSData con información de rutas
            max_walking_distance_km: Distancia máxima de caminata (default: 1 km)
            walking_speed_kmh: Velocidad de caminata asumida (default: 5 km/h)
        """
        self.gtfs = gtfs_data
        self.max_walking_distance = max_walking_distance_km
        self.walking_speed = walking_speed_kmh
    
    def find_nearby_origin_stops(self, origin_coords: Tuple[float, float],
                                 max_stops: int = 5) -> List[Tuple[str, float, float]]:
        """
        Encuentra paradas cercanas al origen.
        
        Args:
            origin_coords: Coordenadas de origen (lat, lon)
            max_stops: Número máximo de paradas a retornar
            
        Returns:
            Lista de tuplas (stop_id, distance_km, walking_time_seconds)
        """
        nearby_stops = self.gtfs.get_nearby_stops(
            origin_coords, 
            margin_km=self.max_walking_distance,
            max_stops=max_stops
        )
        
        # Agregar tiempo de caminata
        result = []
        for stop_id, distance in nearby_stops:
            walking_time = (distance / self.walking_speed) * 3600  # segundos
            result.append((stop_id, distance, walking_time))
        
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
    
    def plan_journey(self, origin_coords: Tuple[float, float],
                    destination_coords: Tuple[float, float],
                    departure_time: datetime,
                    max_transfers: int = 2) -> Optional[Journey]:
        """
        Planifica un viaje desde origen a destino.
        
        Args:
            origin_coords: Coordenadas de origen (lat, lon)
            destination_coords: Coordenadas de destino (lat, lon)
            departure_time: Hora de salida deseada
            max_transfers: Número máximo de transbordos permitidos
            
        Returns:
            Journey con el mejor viaje encontrado, o None si no hay ruta
        """
        journey = Journey(origin_coords, destination_coords)
        
        # Paso 1: Encontrar paradas cercanas al origen
        origin_stops = self.find_nearby_origin_stops(origin_coords, max_stops=3)
        
        if not origin_stops:
            print(f"⚠️  No se encontraron paradas cerca del origen (radio {self.max_walking_distance} km)")
            return None
        
        # Paso 2: Encontrar paradas cercanas al destino
        destination_stops = self.find_nearby_destination_stops(destination_coords, max_stops=3)
        
        if not destination_stops:
            print(f"⚠️  No se encontraron paradas cerca del destino (radio {self.max_walking_distance} km)")
            return None
        
        # Paso 3: Para esta versión básica, usar la parada más cercana al origen
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
        # (Por ahora simplificado - en versión completa usar connection scan algorithm)
        
        # Buscar rutas que pasan por la parada de origen
        routes_at_origin = self._find_routes_at_stop(best_origin_stop)
        
        if not routes_at_origin:
            print(f"⚠️  No se encontraron rutas en parada {best_origin_stop}")
            return None
        
        # Paso 5: Para cada ruta, verificar si llega cerca del destino
        best_route = None
        best_destination_stop = None
        min_total_distance = float('inf')
        
        for route_id in routes_at_origin:
            # Obtener paradas de esta ruta
            if route_id not in self.gtfs.route_stops:
                continue
                
            route_stops = self.gtfs.route_stops[route_id]
            
            # Ver cuál parada de la ruta está más cerca del destino
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
            # Aquí se implementaría búsqueda con transbordos
            return None
        
        # Paso 6: Crear segmento de tránsito
        # Tiempo estimado: asumimos 30 minutos (simplificado)
        transit_start = walk_end
        transit_end = transit_start + timedelta(minutes=30)
        
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
def create_journey_planner(gtfs_data, max_walking_km: float = 1.0,
                          walking_speed_kmh: float = 5.0) -> JourneyPlanner:
    """
    Crea una instancia del planificador de viajes.
    
    Args:
        gtfs_data: Instancia de GTFSData
        max_walking_km: Distancia máxima de caminata en km
        walking_speed_kmh: Velocidad de caminata en km/h
        
    Returns:
        JourneyPlanner configurado
    """
    return JourneyPlanner(gtfs_data, max_walking_km, walking_speed_kmh)
