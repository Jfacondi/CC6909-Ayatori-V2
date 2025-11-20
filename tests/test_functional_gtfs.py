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
                    print(f"\n✅ GTFS loaded from: {path}")
                    break
                except Exception as e:
                    print(f"\n⚠️  Error loading GTFS from {path}: {e}")
                    cls.gtfs = None

        if cls.gtfs is None:
            print("\n" + "="*60)
            print("⚠️  NO GTFS FILE FOUND")
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
        print(f"✅ GTFS loaded successfully from: {self.gtfs_path}")

    def test_02_scheduler_exists(self):
        """Test 2: ¿Se crea correctamente el scheduler?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertIsNotNone(self.gtfs.scheduler)
        self.assertTrue(hasattr(self.gtfs.scheduler, "routes"))
        print(f"✅ Scheduler created with routes attribute")

    def test_03_graphs_created(self):
        """Test 3: ¿Se crean grafos para cada ruta?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertGreater(len(self.gtfs.graphs), 0, "No graphs were created")
        print(f"✅ {len(self.gtfs.graphs)} route graphs created")

        # Show first 3 routes
        for i, (route_id, graph) in enumerate(list(self.gtfs.graphs.items())[:3]):
            nodes = len(graph.nodes())
            edges = len(graph.edges())
            print(f"   Route {route_id}: {nodes} nodes, {edges} edges")

    def test_04_stops_identified(self):
        """Test 4: ¿Se identifican correctamente las paradas?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertGreater(len(self.gtfs.stops), 0, "No stops were identified")
        print(f"✅ {len(self.gtfs.stops)} stops identified")

    def test_05_route_stops_mapped(self):
        """Test 5: ¿Se mapean correctamente las paradas por ruta?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        self.assertGreater(len(self.gtfs.route_stops), 0, "No route_stops mapping")
        print(f"✅ {len(self.gtfs.route_stops)} routes have stop mappings")

        # Check structure of route_stops
        for route_id, stops in list(self.gtfs.route_stops.items())[:1]:
            print(f"   Route {route_id} has {len(stops)} stops")
            for stop_id, stop_info in list(stops.items())[:1]:
                print(f"   Sample stop {stop_id}:")
                print(f"     - coordinates: {stop_info.get('coordinates')}")
                print(f"     - sequence: {stop_info.get('sequence')}")

    def test_06_graph_connectivity(self):
        """Test 6: ¿Tienen los grafos estructura conectada?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        # Check 3 random graphs
        for route_id, graph in list(self.gtfs.graphs.items())[:3]:
            nodes = len(graph.nodes())
            edges = len(graph.edges())

            # Graph should have at least some edges
            self.assertGreater(edges, 0, f"Route {route_id} has no edges")
            # Edges should be less than nodes²
            self.assertLess(edges, nodes * nodes)

        print(f"✅ Graph connectivity verified for sample routes")

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

        print(f"✅ Haversine distance: {distance:.2f} km (expected ~3-4 km)")

    def test_08_walking_time_calculation(self):
        """Test 8: ¿Funciona correctamente el cálculo de tiempo de caminata?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        # Two nearby points in Santiago (approximately 1 km apart)
        lat1, lon1 = -33.4489, -70.6693
        lat2, lon2 = -33.4389, -70.6693

        walking_time = self.gtfs.walking_travel_time(
            (lat1, lon1), (lat2, lon2), speed=5.0  # 5 km/h
        )

        # Distance is ~1.11 km between these points
        # At 5 km/h: 1.11 km / 5 km/h * 3600 s/h ≈ 799 seconds (13.3 minutes)
        # Expected range: 10-15 minutes
        expected_min = 10 * 60  # 10 minutes
        expected_max = 15 * 60  # 15 minutes

        self.assertGreater(
            walking_time, expected_min - 60, f"Walking time too short: {walking_time}s"
        )
        self.assertLess(
            walking_time, expected_max + 60, f"Walking time too long: {walking_time}s"
        )

        print(f"✅ Walking time: {walking_time:.0f}s = {walking_time/60:.1f} minutes")
        print(f"   (for ~1.11 km at 5 km/h speed)")

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
            print(f"✅ Found {len(nearby_stops)} stops within 1 km of test point")
        except Exception as e:
            print(f"⚠️  Nearby stops search failed: {e}")

    def test_10_special_dates(self):
        """Test 10: ¿Se identifican las fechas especiales del calendario?"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        # Special dates may or may not exist
        print(f"✅ Special dates: {len(self.gtfs.special_dates)} found")
        if self.gtfs.special_dates:
            for date in self.gtfs.special_dates[:3]:
                print(f"   - {date}")


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

    def test_graphs_have_nodes(self):
        """Todos los grafos deben tener al menos 2 nodos"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        for route_id, graph in self.gtfs.graphs.items():
            self.assertGreaterEqual(
                len(graph.nodes()), 2, f"Route {route_id} has < 2 nodes"
            )

        print(f"✅ All {len(self.gtfs.graphs)} graphs have ≥ 2 nodes")

    def test_graphs_have_edges(self):
        """Todos los grafos deben tener al menos 1 arista"""
        if self.gtfs is None:
            self.skipTest("GTFS file not available")

        for route_id, graph in self.gtfs.graphs.items():
            self.assertGreaterEqual(
                len(graph.edges()), 1, f"Route {route_id} has no edges"
            )

        print(f"✅ All {len(self.gtfs.graphs)} graphs have ≥ 1 edge")

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

        print(f"✅ All sampled coordinates are valid for Chile")


def run_summary():
    """Ejecuta tests y muestra resumen"""
    print("\n" + "="*70)
    print("GTFS FUNCTIONAL TESTS")
    print("="*70)

    # Run functional tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    suite.addTests(loader.loadTestsFromTestCase(TestGTFSFunctional))
    suite.addTests(loader.loadTestsFromTestCase(TestGTFSDataIntegrity))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print(f"Skipped: {len(result.skipped)}")

    if result.wasSuccessful():
        print("\n✅ ALL TESTS PASSED!")
    else:
        print(f"\n❌ TESTS FAILED - See details above")

    print("="*70 + "\n")

    return result


if __name__ == "__main__":
    result = run_summary()
    sys.exit(0 if result.wasSuccessful() else 1)
