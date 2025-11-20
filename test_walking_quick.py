#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Quick test for walking time calculation and get_nearby_stops method
"""

import sys
import os

# Add the parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ayatori.models.GTFSData import GTFSData

print("=" * 70)
print("QUICK TEST: Walking Time & Nearby Stops")
print("=" * 70)

# GTFS path
GTFS_PATH = "ayatori/data/GTFS/test-data/santiago-gtfs.zip"

print("\n1️⃣  Loading GTFS data...")
try:
    gtfs = GTFSData(GTFS_PATH)
    print("✅ GTFS loaded successfully")
except Exception as e:
    print(f"❌ Error loading GTFS: {e}")
    sys.exit(1)

# Test 1: Walking time calculation
print("\n2️⃣  Testing walking_travel_time()...")
try:
    lat1, lon1 = -33.4489, -70.6693
    lat2, lon2 = -33.4389, -70.6693
    
    walking_time = gtfs.walking_travel_time(
        (lat1, lon1), (lat2, lon2), speed=5.0  # 5 km/h
    )
    
    # Distance is ~1.11 km
    # At 5 km/h: 1.11 km / 5 km/h * 3600 s/h ≈ 799 seconds (13.3 minutes)
    expected_min = 10 * 60  # 10 minutes
    expected_max = 15 * 60  # 15 minutes
    
    print(f"   Walking time: {walking_time:.0f}s = {walking_time/60:.1f} minutes")
    print(f"   (for ~1.11 km at 5 km/h)")
    
    if expected_min - 60 < walking_time < expected_max + 60:
        print("   ✅ Walking time is within expected range")
    else:
        print(f"   ⚠️  Walking time outside expected range ({expected_min-60}s - {expected_max+60}s)")
        
except Exception as e:
    print(f"   ❌ Error in walking_travel_time: {e}")
    import traceback
    traceback.print_exc()

# Test 2: Get nearby stops
print("\n3️⃣  Testing get_nearby_stops()...")
try:
    # Test location: Plaza de Armas, Santiago
    test_location = (-33.4372, -70.6506)
    
    nearby = gtfs.get_nearby_stops(test_location, margin_km=0.5, max_stops=5)
    
    print(f"   Found {len(nearby)} stops within 0.5 km:")
    for stop_id, distance in nearby[:5]:
        print(f"     - Stop {stop_id}: {distance:.3f} km")
    
    if len(nearby) > 0:
        print("   ✅ get_nearby_stops() works correctly")
    else:
        print("   ⚠️  No stops found (may be normal if location has no stops nearby)")
        
except AttributeError as e:
    print(f"   ❌ Method not found: {e}")
except Exception as e:
    print(f"   ❌ Error in get_nearby_stops: {e}")
    import traceback
    traceback.print_exc()

# Test 3: Verify method exists
print("\n4️⃣  Verifying method availability...")
if hasattr(gtfs, 'get_nearby_stops'):
    print("   ✅ GTFSData.get_nearby_stops() method exists")
else:
    print("   ❌ GTFSData.get_nearby_stops() method NOT FOUND")

if hasattr(gtfs, 'walking_travel_time'):
    print("   ✅ GTFSData.walking_travel_time() method exists")
else:
    print("   ❌ GTFSData.walking_travel_time() method NOT FOUND")

if hasattr(gtfs, 'haversine'):
    print("   ✅ GTFSData.haversine() method exists")
else:
    print("   ❌ GTFSData.haversine() method NOT FOUND")

print("\n" + "=" * 70)
print("QUICK TEST COMPLETED")
print("=" * 70)
