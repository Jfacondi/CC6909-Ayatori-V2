# Repository Structure — Ayatori V2

Multimodal public transport journey planner for Santiago de Chile.
Processes GTFS (bus/metro schedules) and OSM (street network) data to find optimal routes between arbitrary coordinates.

---

## Root

| File / Dir | Description |
|---|---|
| `README.md` | Project overview and directory structure (cookiecutter template). |
| `repository.md` | This file — full map of every file in the repository. |
| `todo.md` | Roadmap (phases 1–3) and log of completed advances. |
| `install.md` | Step-by-step setup instructions (conda environment, pip install, data paths). |
| `environment.yml` | Conda environment definition (`rustworkx`, `scipy`, `pygtfs`, `folium`, etc.). |
| `requirements.txt` | pip-compatible dependency list (used when installing without conda). |
| `setup.py` | Makes `ayatori` pip-installable as an editable package (`pip install -e .`). |
| `tasks.py` | [invoke](https://www.pyinvoke.org/) task runner (e.g. `invoke notebook`). |
| `LICENSE` | Project license. |
| `.here` | Empty marker file; used by `pyprojroot` to locate the project root from any subdirectory. |
| `.gitignore` | Git ignore rules. |

### Root-level scripts

| File | Description |
|---|---|
| `demo_funcional.py` | End-to-end demo: loads real Santiago GTFS, computes transfers, runs CSA planner, and prints results. Good starting point to verify the full system works. |
| `compute_all_transfers.py` | Standalone script that computes and saves to JSON the full transfer matrix for all 427 routes in Santiago. Run once to generate the cache used by the planner. |
| `test_interactive.py` | Manual integration tests that can be run without pytest; covers empty GTFSData and OSMGraph initialization patterns. |
| `test_complete_system.py` | Full system smoke test: GTFS load → transfer computation → journey planning pipeline. |
| `test_journey_transfers.py` | Focused test for journey planning with pre-computed transfers. |
| `test_walking_quick.py` | Quick test for walking-time calculations and nearby stop queries. |
| `test_results.log` | Log file from a previous test run (not tracked as source of truth). |

---

## `ayatori/` — Main package

### `ayatori/__init__.py`
Package entry point. Imports and re-exports the main public symbols. Catches `ModuleNotFoundError` for optional dependencies (e.g. `pyrosm`) and sets the corresponding symbol to `None` so the package still imports on systems without those deps.

### `ayatori/README.md`
Short description of the package purpose.

---

### `ayatori/models/` — Core algorithms

| File | Description |
|---|---|
| `__init__.py` | Exports all public model classes and factory functions. |
| `GTFSData.py` | **Central data class.** Reads a GTFS zip via `pygtfs`, builds one `rx.PyDiGraph` per route, and exposes: `route_stops` (stop metadata by route), `get_nearby_stops()` (cKDTree spatial query), `get_route_graph/vertices/edges()`, `get_stop_coords()`, `get_arrival_times()`, `walking_travel_time()`, and `get_or_compute_transfers()` for cached transfer computation. Builds `_stop_to_routes` and `_sorted_route_stops` indices on load for O(1) hot-path lookups. |
| `OSMGraph.py` | Reads a `.osm.pbf` file via `pyrosm` and builds an undirected `rx.PyGraph` of the street network. Maintains `_node_id_to_idx` / `_idx_to_node_id` mappings (rustworkx uses integer indices). Provides `find_nearest_node()`, `find_node_by_id()`, `find_node_by_coordinates()`, and `get_nodes_and_edges()`. Requires `pyrosm` (conda-only on Windows). |
| `ConnectionScanAlgorithm.py` | Dijkstra-based multimodal journey planner. Takes a `GTFSData` instance (and optionally a `TransferManager`), accepts origin/destination coordinates and a departure time, and returns up to N `Journey` objects ranked by duration then transfers. Uses the prebuilt `_stop_to_routes` index and `bisect` on sorted stop sequences for performance. |
| `TransferConnection.py` | Two classes: `TransferConnection` (dataclass — one walking transfer between two stops on different routes) and `TransferManager` (collection of all transfers with O(1) deduplication, `get_transfers_from()`, `get_viable_transfers()`, `save()`/`load()` JSON persistence, and `get_statistics()`). |
| `MultimodalLayer.py` | Unified façade over `GTFSData` + `OSMGraph`. Provides `stops_with_walking_times()` (returns nearby stops with real walking times using OSM Dijkstra or haversine fallback), `get_stop_coords()`, and `walking_time_seconds()`. Use this when you want walking times that respect the actual street topology. |
| `JourneyPlannerV2.py` | Higher-level planner that wraps `ConnectionScanAlgorithm`. Handles the full pipeline (find nearby stops → run CSA → convert to legacy `Journey`/`JourneyLeg` objects). Also includes a simplified fallback planner (`_plan_journey_simple`) for cases where CSA finds nothing. |
| `JourneyPlanner.py` | Original simplified planner (v1). Does not use CSA; picks the closest origin stop and the first route that reaches a destination stop. Useful as a reference baseline but lacks multi-transfer support. |
| `predict_model.py` | Placeholder (cookiecutter template stub). |
| `train_model.py` | Placeholder (cookiecutter template stub). |

---

### `ayatori/data/` — Data ingestion

| File | Description |
|---|---|
| `__init__.py` | Package marker. |
| `make_dataset.py` | Script stub for downloading or generating datasets (cookiecutter template). |

#### `ayatori/data/GTFS/`
Contains GTFS zip archives for different versions of Santiago's public transport network:
- `2023-09-02/GTFS-V99-PO20230902.zip`
- `2023-09-16/GTFS-V100-PO20230916.zip`
- `2023-09-23/GTFS-V101-PO20230923.zip`
- `test-data/santiago-gtfs.zip` — smaller dataset used by tests and demos.

#### `ayatori/data/OSM/`
Street network data and processing scripts:

| File | Description |
|---|---|
| `Santiago.osm.pbf` | OSM extract for the Santiago metropolitan area (binary PBF format). |
| `chile-latest.osm.pbf` | Full Chile OSM extract. |
| `pbf_processor.py` | Original script that loads the PBF with `pyrosm`/`osmnx` and explores the network. Still uses `networkx` and `geopandas` — not yet migrated to rustworkx. |
| `pbf_testing.py` | Scratch testing script for OSM loading. |
| `connection_scan_beta.py` | Early prototype of the connection scan algorithm run directly against OSM data. Kept as historical reference. |
| `nom_test.py` | Small name/nominatim test script. |
| `osmconvert64.exe` | Windows binary for converting/filtering OSM files. |

#### `ayatori/data/Origen-Destino(2012)/`
Origin-destination survey data for Santiago (IX ETapa EOD 2012). Contains PDF reports and zip archives with household survey microdata. Used as a validation reference for route demand.

---

### `ayatori/features/` — Feature engineering

| File | Description |
|---|---|
| `__init__.py` | Exports `build_features`. |
| `build_features.py` | Stub for transforming raw data into model features (cookiecutter template). |

---

### `ayatori/utils/` — Utilities

| File | Description |
|---|---|
| `__init__.py` | Package marker. |
| `paths.py` | `data_dir()` helper — uses `pyprojroot` to locate the project root and return the path to `ayatori/data/` regardless of the working directory. |
| `gtfs_cleaner.py` | `clean_gtfs_stops()` — creates a cleaned copy of a GTFS zip by removing stops with missing or invalid coordinates. Outputs a new zip ready for `GTFSData`. |
| `route_tester.py` | Interactive script to test route lookups against a loaded GTFSData instance. |
| `utils.py` | General utility functions (haversine distance, time formatting, etc.). |

---

### `ayatori/visualization/` — Visualizations

| File | Description |
|---|---|
| `__init__.py` | Exports `visualize`. |
| `visualize.py` | Functions to render routes and stops on interactive Folium maps. |

#### `ayatori/visualization/Imagenes/`
Static images used in reports and notebooks: screenshots of example outputs, maps, GTFS diagrams, metro network, OSM renders, and CSA algorithm diagrams.

---

### `ayatori/CC6909___Memoria__Ayatori.pdf`
Thesis document (Memoria) — academic write-up of the Ayatori system, including problem statement, methodology, algorithm design, and results.

---

## `notebooks/`

| File | Description |
|---|---|
| `algorithm_tester.ipynb` | Jupyter notebook for interactive exploration of the CSA algorithm and GTFS data. |

---

## `examples/`

| File | Description |
|---|---|
| `journey_planner_usage.py` | Annotated usage example showing how to load GTFS data, create a `JourneyPlannerV2`, and plan a journey between two coordinates. |

---

## `tests/`

| File | Description |
|---|---|
| `test_basic.py` | Main pytest suite: import checks, GTFSData method presence, rustworkx graph creation, data directory existence. OSMGraph tests are skipped automatically when `pyrosm` is not installed. |
| `test_functional_gtfs.py` | Functional tests that load real GTFS data and assert correct behavior of `get_nearby_stops`, `_stop_to_routes`, and the transfer computation pipeline. |

---

## `scripts/`

| File | Description |
|---|---|
| `setup_venv.sh` | Shell script to create and configure the conda/venv environment. |

---

## `models/`, `reports/`, `references/`
Empty directories reserved by the cookiecutter template for serialized models, generated reports, and reference documents respectively. Each contains a `.gitkeep` to preserve the directory in git.

---

## `Entrega preliminar.pdf`
Preliminary project delivery document (academic milestone report).

---

## Key dependency map

```
GTFSData ──────────────────────────────────────────────────
  └─ pygtfs        (GTFS zip parsing)
  └─ rustworkx     (rx.PyDiGraph per route)
  └─ scipy.cKDTree (nearest-stop spatial queries)
  └─ TransferManager (via get_or_compute_transfers)

OSMGraph ──────────────────────────────────────────────────
  └─ pyrosm        (PBF parsing — conda only on Windows)
  └─ rustworkx     (rx.PyGraph street network)

ConnectionScanAlgorithm ───────────────────────────────────
  └─ GTFSData      (_stop_to_routes index, _sorted_route_stops)
  └─ TransferManager (optional — validates transfers)

MultimodalLayer ───────────────────────────────────────────
  └─ GTFSData
  └─ OSMGraph      (optional — enables real street routing)

JourneyPlannerV2 ──────────────────────────────────────────
  └─ GTFSData
  └─ ConnectionScanAlgorithm
```
