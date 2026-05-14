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
pip install -e ".[api,viz]"
```

Requiere Python ≥ 3.10. Para soporte OSM (`pyrosm`, `geopandas`), instalar también el extra `geo` — recomendado hacerlo vía conda en Windows por las dependencias geoespaciales binarias.

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

```python
from ayatori.visualization import visualize_journeys
m = visualize_journeys(journeys, gtfs_data=gtfs)
m.save("viaje.html")
```

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
| `GET` | `/config/defaults` | Valores por defecto de `CSAConfig` |
| `GET` | `/config/schema` | Min/max/step por campo para sliders |
| `GET` | `/stops/nearby` | Paradas cercanas a `(lat, lon)` |
| `POST` | `/plan` | Planifica un viaje con configuración opcional |
| `POST` | `/plan/compare` | Ejecuta múltiples variantes de configuración en una sola request |

Ver `api/README.md` para esquemas detallados y variables de entorno.

### Frontend

Página servida en `/` con Leaflet. Click izquierdo = origen, click derecho = destino. Panel lateral con sliders sobre todos los parámetros de `CSAConfig` y modo **comparar** para ejecutar varias configuraciones simultáneas y verlas superpuestas en el mapa.

---

## Arquitectura

| Componente | Archivo | Responsabilidad |
|---|---|---|
| `GTFSData` | `ayatori/models/GTFSData.py` | Lectura del feed, índices espaciales (cKDTree) y por ruta, walking time, caché de transferencias |
| `TransferManager` | `ayatori/models/TransferConnection.py` | Persistencia y consulta O(1) de transferencias precomputadas |
| `ConnectionScanAlgorithm` | `ayatori/models/ConnectionScanAlgorithm.py` | Motor de ruteo multi-target con Dijkstra + Pareto |
| `JourneyPlannerV2` | `ayatori/models/JourneyPlannerV2.py` | Wrapper de alto nivel orientado a casos de uso |
| `OSMGraph` | `ayatori/models/OSMGraph.py` | Red peatonal opcional (rustworkx, requiere pyrosm) |
| `api/` | `api/main.py`, `api/schemas.py` | FastAPI + Pydantic |
| `api/static/` | `index.html`, `app.js` | Frontend Leaflet (sin build step) |

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

Ver [`todo.md`](todo.md). El planificador `JourneyPlanner` (V1) está marcado como `DeprecationWarning` y se removerá en 0.3.0.
