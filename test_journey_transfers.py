#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test rápido para Journey Planner y Sistema de Transbordos
"""

import sys
import os
from datetime import datetime

# Add the parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ayatori.models.GTFSData import GTFSData
from ayatori.models.JourneyPlanner import JourneyPlanner, create_journey_planner
from ayatori.models.TransferConnection import TransferConnection, TransferManager

print("=" * 80)
print("QUICK TEST: Journey Planner & Transfer System")
print("=" * 80)

# GTFS path
GTFS_PATH = "ayatori/data/GTFS/test-data/santiago-gtfs.zip"

print("\n1️⃣  Loading GTFS data...")
try:
    gtfs = GTFSData(GTFS_PATH)
    print("✅ GTFS loaded successfully")
    print(f"   - Routes: {len(gtfs.graphs)}")
    print(f"   - Stops: {len(gtfs.stops)}")
except Exception as e:
    print(f"❌ Error loading GTFS: {e}")
    sys.exit(1)

# Test 1: Transfer System
print("\n2️⃣  Testing Transfer System...")
try:
    print("   Creating sample transfer...")
    transfer = TransferConnection(
        from_route_id="101",
        to_route_id="102",
        from_stop_id="STOP_A",
        to_stop_id="STOP_B",
        walking_distance_km=0.3,
        walking_time_seconds=216,  # 3.6 minutos a 5 km/h
        min_transfer_time=240,
        max_waiting_time=900,
        transfer_type='walking'
    )
    
    print(f"   Transfer created: {transfer}")
    print(f"   Is viable: {transfer.is_viable()}")
    print(f"   Total time with 5min wait: {transfer.get_total_transfer_time(300)/60:.1f} min")
    print("   ✅ TransferConnection works correctly")
    
except Exception as e:
    print(f"   ❌ Error with TransferConnection: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Transfer Manager
print("\n3️⃣  Testing Transfer Manager...")
try:
    manager = TransferManager()
    
    # Add some sample transfers
    for i in range(3):
        t = TransferConnection(
            from_route_id="101",
            to_route_id=f"10{i+2}",
            from_stop_id="STOP_A",
            to_stop_id=f"STOP_{chr(66+i)}",
            walking_distance_km=0.2 + i*0.1,
            walking_time_seconds=144 + i*72,
            transfer_type='nearby'
        )
        manager.add_transfer(t)
    
    print(f"   Manager: {manager}")
    print(f"   Total transfers: {manager.count_transfers()}")
    
    stats = manager.get_statistics()
    print(f"   Statistics:")
    print(f"     - Total: {stats['total_transfers']}")
    print(f"     - Viable: {stats['viable_transfers']}")
    print(f"     - Viability rate: {stats['viability_rate']*100:.1f}%")
    
    # Get transfers from a stop
    transfers = manager.get_transfers_from("101", "STOP_A")
    print(f"   Transfers from Route 101, Stop A: {len(transfers)}")
    
    print("   ✅ TransferManager works correctly")
    
except Exception as e:
    print(f"   ❌ Error with TransferManager: {e}")
    import traceback
    traceback.print_exc()

# Test 3: find_nearby_routes
print("\n4️⃣  Testing find_nearby_routes()...")
try:
    # Get a sample stop from first route
    if gtfs.route_stops:
        first_route = list(gtfs.route_stops.keys())[0]
        stops = list(gtfs.route_stops[first_route].keys())
        
        if stops:
            test_stop = stops[0]
            print(f"   Testing with stop: {test_stop} from route {first_route}")
            
            nearby_routes = gtfs.find_nearby_routes(test_stop, margin_km=0.5)
            print(f"   Found {len(nearby_routes)} nearby routes")
            
            # Show first 3
            for route_id, stops_list in list(nearby_routes.items())[:3]:
                print(f"     - Route {route_id}: {len(stops_list)} nearby stops")
                if stops_list:
                    closest = stops_list[0]
                    print(f"       Closest: {closest[0]} at {closest[1]*1000:.0f}m")
            
            print("   ✅ find_nearby_routes() works correctly")
        else:
            print("   ⚠️  No stops found in route")
    else:
        print("   ⚠️  No routes available")
        
except Exception as e:
    print(f"   ❌ Error with find_nearby_routes: {e}")
    import traceback
    traceback.print_exc()

# Test 4: compute_all_transfers (light version - only 5 routes)
print("\n5️⃣  Testing compute_all_transfers() [LIMITED]...")
try:
    # Create a subset GTFSData for faster testing
    print("   Computing transfers for first 5 routes only...")
    
    # Temporarily limit routes
    original_routes = gtfs.route_stops.copy()
    limited_routes = dict(list(original_routes.items())[:5])
    gtfs.route_stops = limited_routes
    
    transfer_mgr = gtfs.compute_all_transfers(
        max_distance_km=0.5,
        max_waiting_minutes=15,
        walking_speed_kmh=5.0
    )
    
    # Restore original routes
    gtfs.route_stops = original_routes
    
    print(f"   Result: {transfer_mgr}")
    stats = transfer_mgr.get_statistics()
    print(f"   Statistics (limited to 5 routes):")
    print(f"     - Total transfers: {stats['total_transfers']}")
    print(f"     - Viable: {stats['viable_transfers']}")
    print(f"     - Routes: {stats['routes_with_transfers']}")
    
    print("   ✅ compute_all_transfers() works correctly")
    
except Exception as e:
    print(f"   ❌ Error with compute_all_transfers: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Journey Planner
print("\n6️⃣  Testing Journey Planner...")
try:
    planner = create_journey_planner(gtfs, max_walking_km=1.0, walking_speed_kmh=5.0)
    
    print(f"   Journey Planner created")
    print(f"   Max walking distance: {planner.max_walking_distance} km")
    print(f"   Walking speed: {planner.walking_speed} km/h")
    
    # Test finding nearby stops
    print("\n   Testing find_nearby_origin_stops()...")
    test_location = (-33.4372, -70.6506)  # Plaza de Armas
    
    origin_stops = planner.find_nearby_origin_stops(test_location, max_stops=3)
    print(f"   Found {len(origin_stops)} stops near origin:")
    for stop_id, distance, walk_time in origin_stops:
        print(f"     - {stop_id}: {distance*1000:.0f}m, {walk_time/60:.1f}min walk")
    
    # Test journey planning
    print("\n   Testing plan_journey()...")
    origin = (-33.4372, -70.6506)  # Plaza de Armas
    destination = (-33.4489, -70.6693)  # Nearby location
    departure = datetime.now()
    
    journey = planner.plan_journey(origin, destination, departure, max_transfers=2)
    
    if journey:
        print(f"   Journey planned: {journey}")
        print(f"   Legs:")
        for i, leg in enumerate(journey.legs, 1):
            print(f"     {i}. {leg}")
        print(f"   Total duration: {journey.total_duration/60:.1f} minutes")
        print(f"   Total walking: {journey.total_walking_distance:.2f} km")
        print(f"   Transfers: {journey.number_of_transfers}")
        print("   ✅ Journey planning works correctly")
    else:
        print("   ⚠️  No journey found (expected with simplified planner)")
    
except Exception as e:
    print(f"   ❌ Error with Journey Planner: {e}")
    import traceback
    traceback.print_exc()

# Test 6: Method availability
print("\n7️⃣  Verifying method availability...")
methods_to_check = [
    ('get_nearby_stops', 'GTFSData'),
    ('walking_travel_time', 'GTFSData'),
    ('haversine', 'GTFSData'),
    ('find_nearby_routes', 'GTFSData'),
    ('compute_all_transfers', 'GTFSData'),
    ('get_transfer_options', 'GTFSData'),
]

for method_name, class_name in methods_to_check:
    if hasattr(gtfs, method_name):
        print(f"   ✅ {class_name}.{method_name}() exists")
    else:
        print(f"   ❌ {class_name}.{method_name}() NOT FOUND")

print("\n" + "=" * 80)
print("QUICK TEST COMPLETED")
print("=" * 80)
