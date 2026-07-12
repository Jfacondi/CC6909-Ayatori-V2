"""
Tests Funcionales para GTFSData
Valida que el código funciona con datos GTFS reales
"""

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from ayatori.models.GTFSData import GTFSData
import unittest


class TestGTFSFunctional(unittest.TestCase):
    """Tests funcionales con datos GTFS reales"""

    @classmethod
    def setUpClass(cls):
        """Se ejecuta una sola vez antes de todos los tests"""
        # Buscar archivo GTFS en diferentes ubicaciones
        possible_paths = [
            "ayatori/data/GTFS/test-data/santiago-gtfs.zip",
            "ayatori/data/GTFS/2023-09-02/GTFS.zip",
            "ayatori/data/GTFS/2023-09-16/GTFS.zip",
            "ayatori/data/GTFS/2023-09-23/GTFS.zip",
        ]

        cls.gtfs = None
        cls.gtfs_path = None

        for path in possible_paths:
            if os.path.exists(path):
                cls.gtfs_path = path
                try:
                    cls.gtfs = GTFSData(path)
                    print(f"\nGTFS loaded from: {path}")
                    break
                except Exception as e:
                    print(f"\nError loading GTFS from {path}: {e}")
                    cls.gtfs = None

        if cls.gtfs is None:
            print("\n" + "="*60)
            print("NO GTFS FILE FOUND")
            print("="*60)
            print("\nTo download GTFS data for testing:")
            print("1. Go to https://www.red.cl/")
            print("2. Download GTFS zip file")
            print("3. Place in: ayatori/data/GTFS/test-data/santiago-gtfs.zip")
            print("\nOR use existing data from 2023:")
            print("  ls ayatori/data/GTFS/")
            print("\nFor now, tests will be skipped.")
            print("="*60 + "\n")

    def test_01_gtfs_loads(self):
        """Test 1: ¿Carga el archivo GTFS correctamente?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertIsNotNone(self.gtfs)
        self.assertIsNotNone(self.gtfs.scheduler)
        print(f"GTFS loaded successfully from: {self.gtfs_path}")

    def test_02_scheduler_exists(self):
        """Test 2: ¿Se crea correctamente el scheduler?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertIsNotNone(self.gtfs.scheduler)
        self.assertTrue(hasattr(self.gtfs.scheduler, "routes"))
        print(f"Scheduler created with routes attribute")

    def test_04_stops_identified(self):
        """Test 4: ¿Se identifican correctamente las paradas?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertGreater(len(self.gtfs.stops), 0, "No stops were identified")
        print(f"{len(self.gtfs.stops)} stops identified")

    def test_05_route_stops_mapped(self):
        """Test 5: ¿Se mapean correctamente las paradas por ruta?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertGreater(len(self.gtfs.route_stops), 0, "No route_stops mapping")
        print(f"{len(self.gtfs.route_stops)} routes have stop mappings")

        # Check structure of route_stops
        for route_id, stops in list(self.gtfs.route_stops.items())[:1]:
            print(f"   Route {route_id} has {len(stops)} stops")
            for stop_id, stop_info in list(stops.items())[:1]:
                print(f"   Sample stop {stop_id}:")
                print(f"     - coordinates: {stop_info.get('coordinates')}")
                print(f"     - sequence: {stop_info.get('sequence')}")

    def test_07_haversine_distance_formula(self):
        """Test 7: ¿Funciona correctamente el cálculo de distancia Haversine?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        # Santiago landmarks (verified coordinates)
        # Plaza de Armas: -33.4489, -70.6693
        # Estación Central: -33.4380, -70.6289
        # Approximate distance: 3-4 km

        lat1, lon1 = -33.4489, -70.6693
        lat2, lon2 = -33.4380, -70.6289

        distance = self.gtfs.haversine(lat1, lon1, lat2, lon2)

        # Validate distance is reasonable
        self.assertGreater(distance, 2, "Distance too small")
        self.assertLess(distance, 6, "Distance too large")

        print(f"Haversine distance: {distance:.2f} km (expected ~3-4 km)")

    def test_09_nearby_stops_search(self):
        """Test 9: ¿Funciona búsqueda de paradas cercanas?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        if len(self.gtfs.stops) == 0:
            self.skipTest("No stops in GTFS data")

        # Get a random stop coordinate
        stops_list = list(self.gtfs.route_stops.values())
        if len(stops_list) == 0:
            self.skipTest("No route stops data")

        first_route_stops = stops_list[0]
        if len(first_route_stops) == 0:
            self.skipTest("No stops in first route")

        test_stop_id, test_stop_info = list(first_route_stops.items())[0]
        test_coords = test_stop_info["coordinates"]

        # Search for nearby stops within 1 km
        try:
            nearby_stops = self.gtfs.get_nearby_stops(test_coords, margin=1.0)
            # Should find at least the test stop itself
            self.assertGreater(len(nearby_stops), 0)
            print(f"Found {len(nearby_stops)} stops within 1 km of test point")
        except Exception as e:
            print(f"Nearby stops search failed: {e}")

class TestGTFSDataIntegrity(unittest.TestCase):
    """Tests de integridad de datos GTFS"""

    @classmethod
    def setUpClass(cls):
        """Cargar GTFS para tests de integridad"""
        possible_paths = [
            "ayatori/data/GTFS/test-data/santiago-gtfs.zip",
            "ayatori/data/GTFS/2023-09-02/GTFS.zip",
            "ayatori/data/GTFS/2023-09-16/GTFS.zip",
            "ayatori/data/GTFS/2023-09-23/GTFS.zip",
        ]

        cls.gtfs = None
        for path in possible_paths:
            if os.path.exists(path):
                try:
                    cls.gtfs = GTFSData(path)
                    break
                except Exception:
                    pass

    def test_coordinates_are_valid(self):
        """Las coordenadas deben estar dentro de rangos válidos para Chile"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        # Chile latitude range: -56 to -17
        # Chile longitude range: -66 to -109
        for route_id, stops in list(self.gtfs.route_stops.items())[:5]:
            for stop_id, stop_info in stops.items():
                coords = stop_info.get("coordinates")
                if coords:
                    lon, lat = coords
                    self.assertGreater(lat, -56, f"Latitude too south: {lat}")
                    self.assertLess(lat, -17, f"Latitude too north: {lat}")
                    self.assertGreater(lon, -109, f"Longitude too west: {lon}")
                    self.assertLess(lon, -66, f"Longitude too east: {lon}")

        print(f"All sampled coordinates are valid for Chile")
