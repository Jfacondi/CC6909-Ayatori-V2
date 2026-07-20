# Ayatori

Planificador multimodal de viajes urbanos para sistemas con feed GTFS. Combina **Connection Scan Algorithm (CSA)** con transferencias precomputadas, frente de Pareto sobre `(tiempo, transbordos, caminata)` y perfiles de optimización. Empaquetado como librería Python, API REST (FastAPI) y frontend interactivo (Leaflet).

```
GTFS (zip) ──┐
             ├──► GTFSData ──► TransferManager ──► ConnectionScanAlgorithm ──► Journey[] (Pareto)
OSM (pbf) ───┘                        │                       │
   (opcional)                         └── cache JSON ──────────┘
```

---

## Instalación rápida

```bash
git clone <repo> && cd CC6909-Ayatori-V2
python -m venv .venv && . .venv/Scripts/activate     # Windows
# . .venv/bin/activate                                # Linux/macOS
pip install -e ".[api,geo]"
```

Requiere Python ≥ 3.10. Para soporte OSM, instalar el extra `geo` (`osmium`): tiene wheel en Windows (`pip install osmium`), por lo que ya no requiere conda.

## Descarga de datos

Los feeds GTFS y archivos OSM no se versionan. Para descargar los datasets declarados en `ayatori/data/manifest.toml`:

```bash
ayatori-fetch-data --list                  # ver datasets disponibles
ayatori-fetch-data                          # descargar todos
ayatori-fetch-data --only gtfs.2023-09-16   # uno específico
```

Editar `ayatori/data/manifest.toml` para apuntar a un feed más reciente o agregar otros datasets.

---

## Uso por código

### Cargar GTFS y planificar un viaje

```python
from datetime import datetime
from ayatori import GTFSData, ConnectionScanAlgorithm

gtfs = GTFSData("ayatori/data/GTFS/2023-09-16/GTFS-V100-PO20230916.zip")
tm = gtfs.get_or_compute_transfers(cache_path="ayatori/data/cache/transfers.json")

csa = ConnectionScanAlgorithm(gtfs, transfer_manager=tm)
journeys = csa.find_journey(
    origin_coords=(-33.4372, -70.6506),       # Plaza de Armas
    destination_coords=(-33.4489, -70.6693),  # Estación Central
    departure_time=datetime(2023, 9, 4, 8, 0),
    num_alternatives=5,
    profile="balanced",                        # fastest | fewer_transfers | less_walking
)

for j in journeys:
    print(j)
```

### Variar configuración

```python
from ayatori.models.ConnectionScanAlgorithm import CSAConfig

cfg = CSAConfig(
    max_walking_to_stop_km=1.5,
    max_transfers=2,
    walking_speed_kmh=4.5,
)
csa = ConnectionScanAlgorithm(gtfs, transfer_manager=tm, config=cfg)
```

### Visualización

La visualización de itinerarios vive en el **frontend web** (ver [Uso por API](#uso-por-api-docker)):
cada viaje se dibuja sobre el mapa con las *shapes* reales del GTFS, caminatas punteadas y
transbordos con ícono. El antiguo módulo de visualización sobre Folium para notebooks fue
reemplazado por el frontend.

---

## Uso por API (Docker)

```bash
docker compose up --build
# Frontend interactivo:  http://localhost:8000
# Swagger autodocumentado: http://localhost:8000/docs
```

El contenedor monta `ayatori/data/` como volumen. La primera ejecución carga el GTFS y **calcula la matriz de transferencias** (~minutos según el feed); las siguientes leen `ayatori/data/cache/transfers.json` y arrancan en segundos.

### Endpoints principales

| Método | Path | Detalle |
|---|---|---|
| `GET` | `/health` | Estado de carga, conteo de rutas/paradas/transferencias |
| `GET` | `/config/schema` | Min/max/step (+ default) por campo para sliders |
| `GET` | `/stops/nearby` | Paradas cercanas a `(lat, lon)` |
| `POST` | `/plan` | Planifica un viaje con configuración opcional |
| `POST` | `/plan/compare` | Ejecuta múltiples variantes de configuración en una sola request |

Ver `api/README.md` para esquemas detallados y variables de entorno.

### Frontend

Página servida en `/` con Leaflet. Click izquierdo = origen, click derecho = destino. Panel lateral con sliders sobre todos los parámetros de `CSAConfig` y modo **comparar** para ejecutar varias configuraciones simultáneas y verlas superpuestas en el mapa.

### Variables de entorno

La API las lee al arrancar (todas opcionales):

| Variable | Default | Para qué |
|---|---|---|
| `AYATORI_GTFS_PATH` | feed `2023-09-16` | Feed GTFS a cargar (ej. `ayatori/data/GTFS/GTFS_20260425_v3.zip` para el feed 2026). |
| `AYATORI_TRANSFERS_CACHE` | `ayatori/data/cache/transfers.json` | Cache de la matriz de transbordos. |
| `AYATORI_USE_OSM` | `0` | `1` activa el ruteo peatonal real: **las caminatas siguen las calles** en vez de líneas rectas. Requiere `osmium` + un `.pbf`; sin eso degrada a Haversine. |
| `AYATORI_OSM_PBF` | `ayatori/data/OSM/Santiago.osm.pbf` | Archivo OSM para el grafo peatonal. |

```bash
# Feed 2026 + caminatas siguiendo calles
AYATORI_GTFS_PATH=ayatori/data/GTFS/GTFS_20260425_v3.zip \
AYATORI_TRANSFERS_CACHE=ayatori/data/cache/transfers_2026.json \
AYATORI_USE_OSM=1 \
  uvicorn api.main:app --host 127.0.0.1 --port 8000
```

---

## Arquitectura

| Componente | Archivo | Responsabilidad |
|---|---|---|
| `GTFSData` | `ayatori/models/GTFSData.py` | Lectura del feed, índices espaciales (cKDTree) y por ruta, walking time, caché de transferencias |
| `TransferManager` | `ayatori/models/TransferConnection.py` | Persistencia y consulta O(1) de transferencias precomputadas |
| `ConnectionScanAlgorithm` | `ayatori/models/ConnectionScanAlgorithm.py` | Motor de ruteo multi-target con Dijkstra + Pareto |
| `OSMGraph` | `ayatori/models/OSMGraph.py` | Red peatonal opcional (rustworkx, requiere osmium) |
| `api/` | `api/main.py`, `api/schemas.py` | FastAPI + Pydantic |
| `api/static/` | `index.html`, `js/` (módulos), `css/` | Frontend Leaflet (sin build step) |

---

## Algoritmos

El motor combina varias técnicas. El **detalle de implementación de cada una**
(dónde vive, estructuras de datos, paso a paso, complejidad) está en
[`docs/ARQUITECTURA.md` §4](docs/ARQUITECTURA.md).

| Algoritmo / técnica | Dónde | Para qué |
|---|---|---|
| **CSA multi-target** (Dijkstra one-to-many con etiquetas) | `ConnectionScanAlgorithm._connection_scan_multi_target` | Viaje más temprano desde un origen hacia *todos* los destinos candidatos en una corrida |
| **Frente de Pareto 3D** `(llegada, transbordos, caminata)` | `_pareto_filter` | Quedarse con los viajes no dominados |
| **Costo generalizado + perfiles** (estilo OTP) | `_sort_by_profile` | Rankear el frente según el perfil sin romper la admisibilidad de Dijkstra |
| **Búsqueda binaria** (`bisect`) | `_next_boarding` | Primer abordaje de una ruta con horario ≥ ahora, en O(log n) |
| **Expansión de frecuencias** (trip virtual) | `GTFSData._expand_frequencies` | Convertir `frequencies.txt` en despachos discretos |
| **Índice invertido + índices por trip** | `GTFSData._build_route_index` | `parada→rutas` y "siguiente parada del mismo bus" en O(1) |
| **k-d tree** (`cKDTree`) | `GTFSData`/`OSMGraph._build_spatial_index` | Paradas/nodos cercanos en O(log n) |
| **Precómputo de transbordos** | `compute_all_transfers` + `TransferManager` | Viabilidad de transbordo como consulta O(1) |
| **Dijkstra peatonal** (`rustworkx`) | `OSMGraph.shortest_path` | Distancia y geometría real a pie (fallback Haversine) |
| **Calendario** (`calendar` + `calendar_dates`) | `GTFSData.active_services_on` | Servicios activos por fecha, con fallback robusto |
| **Recorte de polyline por proyección** | `GTFSData.get_route_shape_segment` | Trazado real de un tramo entre dos paradas |

---

## Desarrollo

```bash
pip install -e ".[dev]"
pre-commit install
ruff check ayatori api
ruff format ayatori api
```

Linting: `ruff` (incluye reglas de Bugbear, isort, pyupgrade, simplify).
Formato: `ruff format` (compatible con black).
Tipos: `mypy` configurado en modo permisivo en `ayatori/`, estricto en `api/`.

---

## Roadmap

El planificador `JourneyPlanner` (V1) original fue removido; el motor de ruteo es `ConnectionScanAlgorithm`. El trabajo futuro se detalla en el capítulo de conclusiones de la memoria.
