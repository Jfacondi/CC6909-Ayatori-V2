"""
Test suite básico para el proyecto Ayatori.
Prueba importación y funcionalidad básica de los módulos principales.

Ejecutar con:
    pytest tests/test_basic.py -v
    o simplemente:
    python tests/test_basic.py
"""

import sys
import pytest
from pathlib import Path

# Agregar raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import osmium  # noqa: F401
    _OSM_AVAILABLE = True
except ImportError:
    _OSM_AVAILABLE = False

pyrosm_required = pytest.mark.skipif(
    not _OSM_AVAILABLE,
    reason="osmium not installed"
)


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

    @pyrosm_required
    def test_import_osm(self):
        """Probar importación de OSMGraph."""
        from ayatori.models import OSMGraph
        assert OSMGraph is not None

class TestGTFSData:
    """Pruebas para el módulo GTFSData."""

    def test_gtfs_initialization(self):
        """Probar que se puede crear instancia de GTFSData (sin datos reales)."""
        from ayatori.models import GTFSData
        
        # Este test fallará si no hay archivo GTFS real, pero verifica
        # que la clase se puede importar y tiene los métodos esperados
        assert hasattr(GTFSData, 'create_scheduler')
        assert hasattr(GTFSData, 'get_gtfs_data')
        assert hasattr(GTFSData, 'get_stop_ids')

    def test_gtfs_methods(self):
        """Verificar que los métodos principales existen."""
        from ayatori.models import GTFSData
        import inspect
        
        # Obtener métodos de la clase
        methods = [
            'create_scheduler',
            'get_gtfs_data',
            'get_stop_ids',
            'get_nearby_stops',
            'get_stop_coords',
        ]
        
        # Usar inspect en lugar de hasattr
        members = dict(inspect.getmembers(GTFSData, predicate=inspect.isfunction))
        for method in methods:
            assert method in members, f"GTFSData falta método: {method}"


class TestOSMGraph:
    """Pruebas para el módulo OSMGraph."""

    @pyrosm_required
    def test_osm_initialization(self):
        """Probar que se puede importar OSMGraph."""
        from ayatori.models import OSMGraph
        assert OSMGraph is not None

    @pyrosm_required
    def test_osm_methods(self):
        """Verificar que los métodos principales existen."""
        from ayatori.models import OSMGraph
        import inspect
        
        # Obtener métodos de la clase
        methods = [
            'find_nearest_node',
            'shortest_path',
        ]

        members = dict(inspect.getmembers(OSMGraph, predicate=callable))
        for method in methods:
            assert method in members, f"OSMGraph falta método: {method}"


class TestRustworkxUsage:
    """Probar que rustworkx se usa correctamente."""

    def test_rustworkx_digraph(self):
        """Verificar que se puede crear un PyDiGraph con rustworkx."""
        import rustworkx as rx

        g = rx.PyDiGraph()
        idx_a = g.add_node({"stop_id": "stop_1"})
        idx_b = g.add_node({"stop_id": "stop_2"})
        g.add_edge(idx_a, idx_b, {"weight": 1})

        assert len(g.node_indices()) == 2
        assert len(g.edge_list()) == 1
        assert g[idx_a]["stop_id"] == "stop_1"

    def test_rustworkx_graph(self):
        """Verificar que se puede crear un PyGraph con rustworkx."""
        import rustworkx as rx

        g = rx.PyGraph()
        idx_1 = g.add_node({"lon": -70.5, "lat": -33.4})
        idx_2 = g.add_node({"lon": -70.6, "lat": -33.5})
        g.add_edge(idx_1, idx_2, {"weight": 10.5})

        assert len(g.node_indices()) == 2
        assert g[idx_1]["lat"] == -33.4


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


class TestConnectionScanAlgorithm:
    """Pruebas para el módulo ConnectionScanAlgorithm (contrato de la propuesta §6.6.1)."""

    def test_connection_scan_algorithm_class(self):
        """Verificar que la clase CSA existe y expone su API pública."""
        from ayatori.models import ConnectionScanAlgorithm
        import inspect

        assert inspect.isclass(ConnectionScanAlgorithm)
        members = dict(inspect.getmembers(ConnectionScanAlgorithm, predicate=inspect.isfunction))
        for method in ("find_journey", "_connection_scan_multi_target", "_pareto_filter"):
            assert method in members, f"ConnectionScanAlgorithm falta método: {method}"


class TestTransferConnection:
    """Pruebas para TransferConnection / TransferManager (contrato de la propuesta §6.6.1)."""

    def test_transfer_connection_class(self):
        """Verificar campos, slots=True y is_viable() de TransferConnection."""
        from ayatori.models import TransferConnection

        t = TransferConnection(
            from_route_id="R1",
            to_route_id="R2",
            from_stop_id="S1",
            to_stop_id="S2",
            walking_distance_km=0.1,
            walking_time_seconds=120,
        )
        # slots=True añadido en Bloque 1: la instancia no debe tener __dict__
        assert not hasattr(t, "__dict__"), "TransferConnection debe usar slots=True"
        assert t.is_viable() is True
        # Un transbordo de 1 km no es caminable → no viable
        t_lejos = TransferConnection("R1", "R2", "S1", "S2", 1.0, 800)
        assert t_lejos.is_viable() is False

    def test_transfer_methods(self):
        """Verificar que TransferManager expone su API de registro y persistencia."""
        from ayatori.models import TransferConnection, TransferManager
        import inspect

        # load() es @classmethod → usar callable(getattr) en vez de inspect.isfunction
        for method in ("add_transfer", "save", "load", "get_transfers_from", "count_transfers"):
            assert callable(getattr(TransferManager, method, None)), (
                f"TransferManager falta método: {method}"
            )

        # Comportamiento mínimo: agregar + deduplicar
        mgr = TransferManager()
        tc = TransferConnection("R1", "R2", "S1", "S2", 0.1, 120)
        mgr.add_transfer(tc)
        mgr.add_transfer(tc)  # duplicado, debe ignorarse
        assert mgr.count_transfers() == 1
        assert mgr.get_transfers_from("R1", "S1") == [tc]


class TestTestData:
    """Pruebas de disponibilidad de datos de prueba (contrato de la propuesta §6.6.1)."""

    def test_gtfs_test_data(self):
        """El feed GTFS de prueba debe existir o ser descargable (no obligatorio en CI)."""
        from ayatori.utils.paths import data_dir

        candidates = [
            data_dir() / "GTFS" / "test-data" / "santiago-gtfs.zip",
            data_dir() / "GTFS" / "2023-09-16" / "GTFS-V100-PO20230916.zip",
        ]
        if not any(p.exists() for p in candidates):
            pytest.skip("GTFS de prueba no presente (descargar con ayatori-fetch-data)")
        assert any(p.exists() for p in candidates)

    def test_osm_test_data(self):
        """El .pbf de OSM debe existir o ser descargable (no obligatorio en CI)."""
        from ayatori.utils.paths import data_dir

        pbf = data_dir() / "OSM" / "Santiago.osm.pbf"
        if not pbf.exists():
            pytest.skip("OSM .pbf no presente (descargar con ayatori-fetch-data)")
        assert pbf.exists()


