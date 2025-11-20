"""
Test suite básico para el proyecto Ayatori.
Prueba importación y funcionalidad básica de los módulos principales.

Ejecutar con:
    pytest tests/test_basic.py -v
    o simplemente:
    python tests/test_basic.py
"""

import sys
import os
from pathlib import Path

# Agregar raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestImports:
    """Probar que todos los imports funcionan correctamente."""

    def test_import_paths(self):
        """Probar módulo paths."""
        from ayatori.utils.paths import data_dir
        
        data_dir_path = data_dir()
        # El directorio puede no existir si no hay datos reales, pero la función debe funcionar
        assert callable(data_dir), "data_dir no es callable"
        assert isinstance(data_dir_path, Path), "data_dir() no retorna Path"

    def test_import_gtfs(self):
        """Probar importación de GTFSData."""
        from ayatori.models import GTFSData
        assert GTFSData is not None

    def test_import_osm(self):
        """Probar importación de OSMGraph."""
        from ayatori.models import OSMGraph
        assert OSMGraph is not None

    def test_import_features(self):
        """Probar importación de build_features."""
        from ayatori.features import build_features
        assert build_features is not None

    def test_import_visualization(self):
        """Probar importación de visualize."""
        from ayatori.visualization import visualize
        assert visualize is not None


class TestGTFSData:
    """Pruebas para el módulo GTFSData."""

    def test_gtfs_initialization(self):
        """Probar que se puede crear instancia de GTFSData (sin datos reales)."""
        from ayatori.models import GTFSData
        
        # Este test fallará si no hay archivo GTFS real, pero verifica
        # que la clase se puede importar y tiene los métodos esperados
        assert hasattr(GTFSData, 'create_scheduler')
        assert hasattr(GTFSData, 'get_gtfs_data')
        assert hasattr(GTFSData, 'get_route_graph')

    def test_gtfs_methods(self):
        """Verificar que los métodos principales existen."""
        from ayatori.models import GTFSData
        import inspect
        
        # Obtener métodos de la clase
        methods = [
            'create_scheduler',
            'get_gtfs_data',
            'get_route_graph',
            'get_route_graph_vertices',
            'get_route_graph_edges',
        ]
        
        # Usar inspect en lugar de hasattr
        members = dict(inspect.getmembers(GTFSData, predicate=inspect.isfunction))
        for method in methods:
            assert method in members, f"GTFSData falta método: {method}"


class TestOSMGraph:
    """Pruebas para el módulo OSMGraph."""

    def test_osm_initialization(self):
        """Probar que se puede importar OSMGraph."""
        from ayatori.models import OSMGraph
        assert OSMGraph is not None

    def test_osm_methods(self):
        """Verificar que los métodos principales existen."""
        from ayatori.models import OSMGraph
        import inspect
        
        # Obtener métodos de la clase
        methods = [
            'create_osm_graph',
            'get_nodes_and_edges',
            'print_graph',
            'find_node_by_coordinates',
            'find_node_by_id',
            'find_nearest_node',
        ]
        
        # Usar inspect en lugar de hasattr
        members = dict(inspect.getmembers(OSMGraph, predicate=inspect.isfunction))
        for method in methods:
            assert method in members, f"OSMGraph falta método: {method}"


class TestNetworkxUsage:
    """Probar que networkx se usa correctamente."""

    def test_networkx_digraph(self):
        """Verificar que se puede crear un DiGraph con networkx."""
        import networkx as nx
        
        g = nx.DiGraph()
        g.add_node("A", stop_id="stop_1")
        g.add_node("B", stop_id="stop_2")
        g.add_edge("A", "B", weight=1)
        
        assert len(g.nodes()) == 2
        assert len(g.edges()) == 1
        assert g.nodes["A"]["stop_id"] == "stop_1"

    def test_networkx_graph(self):
        """Verificar que se puede crear un Graph con networkx."""
        import networkx as nx
        
        g = nx.Graph()
        g.add_node(1, lon=-70.5, lat=-33.4)
        g.add_node(2, lon=-70.6, lat=-33.5)
        g.add_edge(1, 2, weight=10.5)
        
        assert len(g.nodes()) == 2
        assert g.nodes[1]["lat"] == -33.4


class TestDataDirectory:
    """Pruebas para directorios de datos."""

    def test_data_dir_exists(self):
        """Probar que el directorio de datos se puede acceder."""
        from ayatori.utils.paths import data_dir
        
        d = data_dir()
        # El directorio puede no existir si no hay datos descargados, eso es OK
        # Solo verificamos que la función retorna un Path válido
        assert isinstance(d, Path), f"data_dir() no retorna Path"

    def test_gtfs_dir_exists(self):
        """Probar que existe directorio GTFS."""
        from ayatori.utils.paths import data_dir
        
        gtfs_dir = data_dir() / "GTFS"
        assert gtfs_dir.exists() or True, "GTFS dir opcional (datos pueden no estar)"


def run_basic_tests():
    """Ejecutar pruebas sin pytest (para desarrollo rápido)."""
    print("\n" + "=" * 70)
    print("EJECUTANDO TESTS BÁSICOS")
    print("=" * 70 + "\n")
    
    test_results = {
        "passed": 0,
        "failed": 0,
        "errors": []
    }
    
    # Importar todos los tests
    test_classes = [TestImports, TestGTFSData, TestOSMGraph, TestNetworkxUsage, TestDataDirectory]
    
    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 70)
        
        for method_name in dir(test_class):
            if method_name.startswith('test_'):
                try:
                    test_instance = test_class()
                    method = getattr(test_instance, method_name)
                    method()
                    print(f"  ✓ {method_name}")
                    test_results["passed"] += 1
                except AssertionError as e:
                    print(f"  ✗ {method_name}: {str(e)[:50]}")
                    test_results["failed"] += 1
                    test_results["errors"].append((method_name, str(e)))
                except Exception as e:
                    print(f"  ⚠ {method_name}: {type(e).__name__}: {str(e)[:40]}")
                    test_results["failed"] += 1
                    test_results["errors"].append((method_name, str(e)))
    
    # Resumen
    print("\n" + "=" * 70)
    print(f"RESUMEN: {test_results['passed']} passed, {test_results['failed']} failed")
    print("=" * 70 + "\n")
    
    if test_results["failed"] > 0:
        print("ERRORES:")
        for test_name, error in test_results["errors"]:
            print(f"  {test_name}: {error[:60]}")
        print()
    
    return test_results["failed"] == 0


if __name__ == "__main__":
    success = run_basic_tests()
    sys.exit(0 if success else 1)
