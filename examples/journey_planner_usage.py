#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
EJEMPLO DE USO: Journey Planner y Sistema de Transbordos

Este archivo muestra cómo usar las nuevas funcionalidades implementadas:
- Journey Planner para planificar viajes
- Sistema de Transbordos para encontrar conexiones entre rutas
- Integración de caminata en viajes
"""

from datetime import datetime
from ayatori.models import (
    GTFSData, 
    JourneyPlanner, 
    create_journey_planner,
    TransferConnection,
    TransferManager
)

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 1: Cargar GTFS y explorar datos básicos
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 1: Cargando datos GTFS")
print("-" * 70)

# Cargar datos GTFS
gtfs = GTFSData("ayatori/data/GTFS/test-data/santiago-gtfs.zip")

print(f"✅ GTFS cargado")
print(f"   Rutas: {len(gtfs.graphs)}")
print(f"   Paradas: {len(gtfs.stops)}")
print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 2: Buscar paradas cercanas a una ubicación
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 2: Buscar paradas cercanas")
print("-" * 70)

# Coordenadas de Plaza de Armas, Santiago
plaza_armas = (-33.4372, -70.6506)

# Buscar paradas dentro de 500 metros
nearby_stops = gtfs.get_nearby_stops(
    location_coords=plaza_armas,
    margin_km=0.5,
    max_stops=5
)

print(f"📍 Ubicación: Plaza de Armas ({plaza_armas[0]}, {plaza_armas[1]})")
print(f"🔍 Paradas cercanas (radio 500m):")
for stop_id, distance in nearby_stops:
    print(f"   - {stop_id}: {distance*1000:.0f} metros")
print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 3: Calcular tiempo de caminata
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 3: Calcular tiempo de caminata")
print("-" * 70)

# Dos ubicaciones
origen = (-33.4489, -70.6693)
destino = (-33.4389, -70.6693)

# Calcular tiempo de caminata a 5 km/h
walking_time = gtfs.walking_travel_time(
    stop_coords=origen,
    location_coords=destino,
    speed=5.0
)

print(f"🚶 Origen: {origen}")
print(f"🎯 Destino: {destino}")
print(f"⏱️  Tiempo de caminata: {walking_time:.0f} segundos ({walking_time/60:.1f} minutos)")
print(f"   Velocidad: 5 km/h")
print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 4: Encontrar rutas cercanas a una parada
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 4: Encontrar rutas cercanas a una parada")
print("-" * 70)

# Tomar una parada de ejemplo
if gtfs.route_stops:
    sample_route = list(gtfs.route_stops.keys())[0]
    sample_stop = list(gtfs.route_stops[sample_route].keys())[0]
    
    print(f"📍 Parada de referencia: {sample_stop} (Ruta {sample_route})")
    
    # Encontrar rutas cercanas
    nearby_routes = gtfs.find_nearby_routes(
        stop_id=sample_stop,
        margin_km=0.5
    )
    
    print(f"🚍 Rutas con paradas cercanas: {len(nearby_routes)}")
    
    # Mostrar primeras 3
    for route_id in list(nearby_routes.keys())[:3]:
        stops_list = nearby_routes[route_id]
        closest_stop, closest_dist = stops_list[0]
        print(f"   - Ruta {route_id}: {len(stops_list)} paradas cercanas")
        print(f"     Más cercana: {closest_stop} a {closest_dist*1000:.0f}m")
    print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 5: Calcular transferencias entre rutas (versión reducida)
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 5: Sistema de transferencias")
print("-" * 70)

# Para ejemplo rápido, usar solo 3 rutas
print("⚠️  Calculando transferencias para primeras 3 rutas (ejemplo rápido)...")

# Guardar rutas originales
original_routes = gtfs.route_stops.copy()

# Limitar a 3 rutas para ejemplo
limited_routes = dict(list(original_routes.items())[:3])
gtfs.route_stops = limited_routes

# Calcular transferencias
transfer_manager = gtfs.compute_all_transfers(
    max_distance_km=0.5,
    max_waiting_minutes=15,
    walking_speed_kmh=5.0
)

# Restaurar rutas originales
gtfs.route_stops = original_routes

# Mostrar estadísticas
stats = transfer_manager.get_statistics()
print(f"\n📊 Estadísticas del sistema de transferencias:")
print(f"   - Total de transferencias: {stats['total_transfers']}")
print(f"   - Transferencias viables: {stats['viable_transfers']}")
print(f"   - Tasa de viabilidad: {stats['viability_rate']*100:.1f}%")
print(f"   - Rutas con transferencias: {stats['routes_with_transfers']}")
print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 6: Obtener opciones de transbordo desde una parada
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 6: Opciones de transbordo desde una parada")
print("-" * 70)

# Obtener transferencias desde primera ruta/parada
if limited_routes:
    first_route = list(limited_routes.keys())[0]
    first_stop = list(limited_routes[first_route].keys())[0]
    
    # Configurar manager en gtfs
    gtfs.transfer_manager = transfer_manager
    
    # Obtener opciones de transbordo
    transfers = gtfs.get_transfer_options(first_route, first_stop, viable_only=True)
    
    print(f"🚏 Desde Ruta {first_route}, Parada {first_stop}:")
    print(f"   Opciones de transbordo: {len(transfers)}")
    
    # Mostrar primeras 3
    for transfer in transfers[:3]:
        print(f"   - Hacia Ruta {transfer.to_route_id}")
        print(f"     Parada destino: {transfer.to_stop_id}")
        print(f"     Distancia: {transfer.walking_distance_km*1000:.0f}m")
        print(f"     Tiempo: {transfer.walking_time_seconds:.0f}s")
    print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 7: Crear un Journey Planner
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 7: Journey Planner")
print("-" * 70)

# Crear planificador de viajes
planner = create_journey_planner(
    gtfs_data=gtfs,
    max_walking_km=1.0,
    walking_speed_kmh=5.0
)

print(f"🗺️  Journey Planner creado")
print(f"   Distancia máxima de caminata: {planner.max_walking_distance} km")
print(f"   Velocidad de caminata: {planner.walking_speed} km/h")

# Buscar paradas cercanas al origen
origin_location = (-33.4372, -70.6506)  # Plaza de Armas
origin_stops = planner.find_nearby_origin_stops(origin_location, max_stops=3)

print(f"\n🏁 Origen: Plaza de Armas")
print(f"   Paradas cercanas:")
for stop_id, distance, walk_time in origin_stops:
    print(f"   - {stop_id}: {distance*1000:.0f}m, {walk_time/60:.1f}min de caminata")

# Planificar un viaje simple
print(f"\n🎯 Planificando viaje...")
destination = (-33.4489, -70.6693)
departure = datetime.now()

journey = planner.plan_journey(
    origin_coords=origin_location,
    destination_coords=destination,
    departure_time=departure,
    max_transfers=2
)

if journey:
    print(f"✅ Viaje planificado:")
    print(f"   {journey}")
    print(f"\n   Segmentos del viaje:")
    for i, leg in enumerate(journey.legs, 1):
        print(f"   {i}. {leg}")
else:
    print(f"⚠️  No se encontró ruta directa")
    print(f"   (El planificador busca rutas directas en esta versión)")

print()

# ═══════════════════════════════════════════════════════════════════════════
# EJEMPLO 8: Crear transferencia manual
# ═══════════════════════════════════════════════════════════════════════════

print("EJEMPLO 8: Crear transferencia manual")
print("-" * 70)

# Crear una transferencia entre dos rutas
transfer = TransferConnection(
    from_route_id="101",
    to_route_id="102",
    from_stop_id="PARADA_A",
    to_stop_id="PARADA_B",
    walking_distance_km=0.3,
    walking_time_seconds=216,  # 3.6 minutos
    min_transfer_time=240,      # 4 minutos
    max_waiting_time=900,       # 15 minutos
    transfer_type='walking'
)

print(f"🔄 Transferencia creada:")
print(f"   {transfer}")
print(f"   ¿Es viable?: {transfer.is_viable()}")
print(f"   Tiempo total (con 5 min espera): {transfer.get_total_transfer_time(300)/60:.1f} min")

# Convertir a diccionario
transfer_dict = transfer.to_dict()
print(f"\n   Como diccionario:")
for key, value in transfer_dict.items():
    print(f"     {key}: {value}")

print()

# ═══════════════════════════════════════════════════════════════════════════
# RESUMEN
# ═══════════════════════════════════════════════════════════════════════════

print("=" * 70)
print("RESUMEN DE FUNCIONALIDADES DISPONIBLES")
print("=" * 70)
print("""
✅ CAMINATA:
   - gtfs.haversine(): Calcular distancia entre puntos
   - gtfs.walking_travel_time(): Calcular tiempo de caminata
   - gtfs.get_nearby_stops(): Buscar paradas cercanas

✅ TRANSBORDOS:
   - gtfs.find_nearby_routes(): Encontrar rutas cercanas
   - gtfs.compute_all_transfers(): Calcular matriz de transferencias
   - gtfs.get_transfer_options(): Obtener opciones de transbordo
   - TransferConnection: Representar un transbordo
   - TransferManager: Administrar transferencias

✅ JOURNEY PLANNER:
   - JourneyPlanner: Planificador de viajes
   - Journey: Representa viaje completo
   - JourneyLeg: Representa segmento de viaje
   - create_journey_planner(): Factory function

📚 Para más información, ver:
   - ayatori/models/JourneyPlanner.py
   - ayatori/models/TransferConnection.py
   - ayatori/models/GTFSData.py (métodos nuevos)
""")
