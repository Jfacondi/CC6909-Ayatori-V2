#!/usr/bin/env python3
"""
Script interactivo para testear y debuggear el código de Ayatori.

Uso:
    python scripts/interactive_debug.py

Permite ejecutar tests específicos y ver el estado del código.
"""

import sys
from pathlib import Path

# Agregar raíz al path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import inspect


def print_menu():
    """Mostrar menú principal."""
    print("\n" + "=" * 70)
    print("TESTING INTERACTIVO AYATORI")
    print("=" * 70)
    print("\nSelecciona un test:")
    print("  1. Imports básicos")
    print("  2. GTFSData - Mostrar métodos disponibles")
    print("  3. OSMGraph - Mostrar métodos disponibles")
    print("  4. Crear grafo GTFS vacío (ejemplo)")
    print("  5. Crear grafo OSM vacío (ejemplo)")
    print("  6. Listar archivos de datos disponibles")
    print("  7. Verificar dependencias")
    print("  0. Salir")
    print()


def test_imports():
    """Probar imports básicos."""
    print("\n" + "-" * 70)
    print("TEST: Imports Básicos")
    print("-" * 70 + "\n")
    
    modules = [
        ("ayatori.utils.paths", ["data_dir"]),
        ("ayatori.data", ["make_dataset"]),
        ("ayatori.models", ["GTFSData", "OSMGraph"]),
        ("ayatori.features", ["build_features"]),
        ("ayatori.visualization", ["visualize"]),
    ]
    
    success = 0
    failed = 0
    
    for module_name, items in modules:
        try:
            mod = __import__(module_name, fromlist=items)
            for item in items:
                getattr(mod, item)
            print(f"✓ {module_name}")
            success += 1
        except Exception as e:
            print(f"✗ {module_name}: {str(e)[:50]}")
            failed += 1
    
    print(f"\nResultado: {success} OK, {failed} fallidos")


def test_gtfs_methods():
    """Mostrar métodos de GTFSData."""
    print("\n" + "-" * 70)
    print("TEST: GTFSData - Métodos Disponibles")
    print("-" * 70 + "\n")
    
    from ayatori.models import GTFSData
    
    # Obtener métodos públicos
    methods = [m for m in dir(GTFSData) if not m.startswith('_')]
    
    print(f"Total de atributos públicos: {len(methods)}\n")
    print("Métodos y atributos:")
    for method in sorted(methods):
        attr = getattr(GTFSData, method)
        type_name = type(attr).__name__
        
        # Mostrar solo métodos (function)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
                print(f"  • {method}{sig}")
            except:
                print(f"  • {method}")


def test_osm_methods():
    """Mostrar métodos de OSMGraph."""
    print("\n" + "-" * 70)
    print("TEST: OSMGraph - Métodos Disponibles")
    print("-" * 70 + "\n")
    
    from ayatori.models import OSMGraph
    
    # Obtener métodos públicos
    methods = [m for m in dir(OSMGraph) if not m.startswith('_')]
    
    print(f"Total de atributos públicos: {len(methods)}\n")
    print("Métodos y atributos:")
    for method in sorted(methods):
        attr = getattr(OSMGraph, method)
        type_name = type(attr).__name__
        
        # Mostrar solo métodos (function)
        if callable(attr):
            try:
                sig = inspect.signature(attr)
                print(f"  • {method}{sig}")
            except:
                print(f"  • {method}")


def test_empty_gtfs():
    """Crear grafo GTFS vacío como ejemplo."""
    print("\n" + "-" * 70)
    print("TEST: Crear Grafo GTFS Vacío (Ejemplo)")
    print("-" * 70 + "\n")

    import rustworkx as rx

    # Simular lo que hace GTFSData
    g = rx.PyDiGraph()
    node_map = {}
    idx_to_node = {}

    stops = ["stop_001", "stop_002", "stop_003"]
    for stop in stops:
        idx = g.add_node({"stop_id": stop})
        node_map[stop] = idx
        idx_to_node[idx] = stop

    g.add_edge(node_map["stop_001"], node_map["stop_002"],
               {"weight": 1, "u": "stop_001", "v": "stop_002"})
    g.add_edge(node_map["stop_002"], node_map["stop_003"],
               {"weight": 1, "u": "stop_002", "v": "stop_003"})

    node_ids = [idx_to_node[i] for i in g.node_indices()]
    edges = [(idx_to_node[u], idx_to_node[v]) for u, v in g.edge_list()]

    print(f"✓ Grafo creado exitosamente")
    print(f"  - Nodos: {len(g.node_indices())}")
    print(f"  - Aristas: {len(g.edge_list())}")
    print(f"  - Nodos: {node_ids}")
    print(f"  - Aristas: {edges}")


def test_empty_osm():
    """Crear grafo OSM vacío como ejemplo."""
    print("\n" + "-" * 70)
    print("TEST: Crear Grafo OSM Vacío (Ejemplo)")
    print("-" * 70 + "\n")

    import rustworkx as rx
    import numpy as np

    # Simular lo que hace OSMGraph
    g = rx.PyGraph()
    node_id_to_idx = {}
    idx_to_node_id = {}

    raw_nodes = [
        (1, {"lon": -70.5, "lat": -33.4}),
        (2, {"lon": -70.51, "lat": -33.41}),
        (3, {"lon": -70.52, "lat": -33.42}),
    ]

    for node_id, attrs in raw_nodes:
        idx = g.add_node({**attrs, "node_id": node_id})
        node_id_to_idx[node_id] = idx
        idx_to_node_id[idx] = node_id

    coords = np.array([[-70.5, -33.4], [-70.51, -33.41], [-70.52, -33.42]])
    d01 = np.linalg.norm(coords[0] - coords[1])
    g.add_edge(node_id_to_idx[1], node_id_to_idx[2], {"weight": d01, "length": d01})

    d12 = np.linalg.norm(coords[1] - coords[2])
    g.add_edge(node_id_to_idx[2], node_id_to_idx[3], {"weight": d12, "length": d12})

    print(f"✓ Grafo OSM creado exitosamente")
    print(f"  - Nodos: {len(g.node_indices())}")
    print(f"  - Aristas: {len(g.edge_list())}")
    print(f"  - Nodos con coords:")
    for idx in g.node_indices():
        data = g[idx]
        print(f"    - Node {data['node_id']}: lon={data['lon']}, lat={data['lat']}")


def test_data_files():
    """Listar archivos de datos disponibles."""
    print("\n" + "-" * 70)
    print("TEST: Archivos de Datos Disponibles")
    print("-" * 70 + "\n")
    
    from ayatori.utils.paths import data_dir
    
    data_path = data_dir()
    print(f"Directorio de datos: {data_path}")
    print(f"Existe: {data_path.exists()}\n")
    
    if data_path.exists():
        print("Contenidos:")
        for item in sorted(data_path.iterdir()):
            if item.is_dir():
                print(f"  📁 {item.name}/")
                # Listar subdirectorios
                for subitem in sorted(item.iterdir()):
                    if subitem.is_dir():
                        print(f"     📁 {subitem.name}/")
                    else:
                        size = subitem.stat().st_size / (1024 * 1024)  # MB
                        print(f"     📄 {subitem.name} ({size:.2f} MB)")
            else:
                print(f"  📄 {item.name}")
    else:
        print("⚠️  El directorio de datos no existe.")
        print("   Debes descargar los datos GTFS y OSM para pruebas funcionales.")


def test_dependencies():
    """Verificar dependencias instaladas."""
    print("\n" + "-" * 70)
    print("TEST: Verificación de Dependencias")
    print("-" * 70 + "\n")
    
    deps = [
        "numpy",
        "pandas",
        "scipy",
        "scikit-learn",
        "tensorflow",
        "keras",
        "matplotlib",
        "seaborn",
        "plotly",
        "folium",
        "rustworkx",
        "networkx",
        "pygtfs",
        "pyrosm",
        "geopy",
        "jupyter",
        "jupyterlab",
        "black",
        "pylint",
    ]
    
    installed = []
    missing = []
    
    for dep in deps:
        try:
            mod = __import__(dep)
            version = getattr(mod, "__version__", "?")
            installed.append((dep, version))
        except ImportError:
            missing.append(dep)
    
    print(f"Instaladas ({len(installed)}):")
    for dep, version in sorted(installed):
        print(f"  ✓ {dep} ({version})")
    
    if missing:
        print(f"\nFaltantes ({len(missing)}):")
        for dep in sorted(missing):
            print(f"  ✗ {dep}")
    else:
        print("\n✓ Todas las dependencias están instaladas")


def main():
    """Función principal."""
    while True:
        print_menu()
        choice = input("Opción: ").strip()
        
        if choice == "0":
            print("\n¡Hasta luego!\n")
            break
        elif choice == "1":
            test_imports()
        elif choice == "2":
            test_gtfs_methods()
        elif choice == "3":
            test_osm_methods()
        elif choice == "4":
            test_empty_gtfs()
        elif choice == "5":
            test_empty_osm()
        elif choice == "6":
            test_data_files()
        elif choice == "7":
            test_dependencies()
        else:
            print("❌ Opción inválida. Intenta de nuevo.")
        
        input("\nPresiona Enter para continuar...")


if __name__ == "__main__":
    main()
