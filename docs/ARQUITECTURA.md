# Arquitectura de Ayatori V2

Documento de referencia sobre la **distribución de carpetas**, **cómo interactúan
los archivos** y los **algoritmos** que sostienen el planificador multimodal de
viajes urbanos Ayatori.

Ayatori es un planificador de viajes sobre feeds **GTFS** (transporte público) con
red peatonal **OSM** opcional. Su motor es el **Connection Scan Algorithm (CSA)**
con transferencias precomputadas, frente de Pareto 3D y perfiles de optimización.
Se entrega como librería Python, API REST (FastAPI) y frontend interactivo (Leaflet).

---

## 1. Vista general del flujo de datos

```
                  manifest.toml
                       │
              ayatori-fetch-data (data/fetch.py)
                       │
        ┌──────────────┴───────────────┐
   GTFS (.zip)                     OSM (.pbf)   ← opcional
        │                              │
   gtfs_cleaner.py                     │
        │                              │
        ▼                              ▼
   ┌─────────┐                    ┌──────────┐
   │ GTFSData│◄───── osm_graph ───│ OSMGraph │  (ruteo peatonal real)
   └────┬────┘                    └──────────┘
        │  índices: cKDTree espacial, stop→rutas,
        │  trips, dispatches (frequencies), calendario, shapes
        │
        ├──► get_or_compute_transfers ──► TransferManager  (cache JSON)
        │                                      │
        ▼                                      ▼
   ┌──────────────────────────────────────────────┐
   │        ConnectionScanAlgorithm (CSA)          │
   │  Dijkstra multi-target + Pareto 3D + perfiles │
   └──────────────────────┬───────────────────────┘
                          │  list[Journey]
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
  JourneyPlannerV2   visualize.py        api/main.py
  (wrapper alto       (mapas Folium)     (FastAPI /plan, /compare)
   nivel)                                      │
                                          api/static/ (Leaflet)
```

La regla de oro del sistema: **cargar GTFS y la matriz de transferencias una sola
vez** (caro, minutos), y a partir de ahí cada consulta de viaje construye un CSA
liviano que sólo referencia esas estructuras compartidas (barato, milisegundos).

---

## 2. Distribución de carpetas

```
CC6909-Ayatori-V2/
├── ayatori/              ← librería principal (paquete instalable)
│   ├── models/           ← núcleo: datos, motor CSA, transbordos, OSM
│   ├── data/             ← descarga y manifiesto de datasets
│   ├── features/         ← (reservado, vacío)
│   ├── utils/            ← limpieza GTFS, paths, helpers legacy
│   └── visualization/    ← render de viajes en mapas Folium
├── api/                  ← capa FastAPI + frontend estático
│   └── static/           ← index.html + app.js (Leaflet, sin build)
├── scripts/              ← demos, smoke tests, cómputos batch
├── tests/                ← pytest (imports + funcional con GTFS real)
├── notebooks/            ← Jupyter (benchmarks, exploración)
├── examples/             ← ejemplo de uso de la librería
├── docs/                 ← propuesta, entrega preliminar, este documento
├── reports/figures/      ← salidas de gráficos/figuras
├── Dockerfile, docker-compose.yml
├── pyproject.toml, requirements.txt, environment.yml
└── tasks.py, .pre-commit-config.yaml
```

### 2.1 `ayatori/models/` — el corazón del sistema

| Archivo | Responsabilidad | Clases / funciones públicas |
|---|---|---|
| `GTFSData.py` | Carga el feed GTFS y construye **todos los índices** de ruteo. | `GTFSData`, `.get_or_compute_transfers()`, `.get_nearby_stops()`, `.active_services_on()`, `.get_route_shape_segment()` |
| `ConnectionScanAlgorithm.py` | **Motor de ruteo**: Dijkstra multi-target + Pareto + perfiles. | `ConnectionScanAlgorithm`, `CSAConfig`, `Journey`, `create_csa_planner()` |
| `TransferConnection.py` | Modela un transbordo y administra la matriz precomputada. | `TransferConnection`, `TransferManager` |
| `OSMGraph.py` | Red peatonal real (caminos OSM) para distancias/geometría a pie. | `OSMGraph`, `.shortest_path()`, `.from_file()` |
| `JourneyPlannerV2.py` | **Wrapper de alto nivel** sobre el CSA, orientado a casos de uso. | `JourneyPlannerV2`, `create_journey_planner_v2()` |
| `JourneyPlanner.py` | V1 **deprecado** (`DeprecationWarning`); se elimina en 0.3.0. | `JourneyPlanner` (legacy) |
| `__init__.py` | Reexporta el API público; carga `OSMGraph` de forma opcional. | — |

### 2.2 `ayatori/data/` — adquisición de datos

| Archivo | Función |
|---|---|
| `manifest.toml` | Declara los datasets (GTFS Santiago 2023-09-16, OSM Chile): URL, destino, SHA256. |
| `fetch.py` | CLI `ayatori-fetch-data`: descarga con verificación de hash y extrae ZIPs. |
| `make_dataset.py` | Reservado (vacío). |

### 2.3 `ayatori/utils/` — utilidades

| Archivo | Función |
|---|---|
| `gtfs_cleaner.py` | `clean_gtfs_stops()`: descarta paradas sin coordenadas válidas (pathway nodes del feed 2026) y genera un ZIP limpio. Lo invoca `GTFSData.create_scheduler`. |
| `paths.py` | Rutas relativas a la raíz del repo vía `pyprojroot` (`data_dir`, `models_dir`, …). |
| `utils.py` | Helpers de ruteo y densidad de paradas (mayormente legacy, pre-CSA). |
| `route_tester.py` | Demo/prueba de ruteo v1 (legacy). |

### 2.4 `ayatori/visualization/`

`visualize.py` renderiza objetos `Journey` (de CSA o V2) en mapas **Folium**
interactivos con capas alternables (`visualize_journey`, `visualize_journeys`,
`visualize_routes`, `visualize_stops`). Usa las shapes/polylines que expone
`GTFSData` para dibujar trazados reales en vez de líneas rectas.

### 2.5 `api/` — servicio REST + frontend

| Archivo | Función |
|---|---|
| `main.py` | App FastAPI. En el `lifespan` carga GTFS + (opcional) OSM + matriz de transbordos + shapes sintéticas una sola vez; expone `/health`, `/config/*`, `/stops/nearby`, `/geocode`, `/plan`, `/plan/compare`. |
| `schemas.py` | DTOs Pydantic (`PlanRequest`, `ConfigOverride`, `SegmentDTO`, …) y `journey_to_dto()` que serializa un `Journey` a JSON. |
| `static/index.html` + `app.js` | Frontend Leaflet sin build: click izq=origen, der=destino, sliders de `CSAConfig`, modo *comparar*. |

### 2.6 Resto

- **`scripts/`**: `compute_all_transfers.py` (precómputo batch), `demo_funcional.py`
  (demo end-to-end), `run_system_checks.py`, `smoke_transfers.py`, `smoke_walking.py`,
  `interactive_debug.py`, `setup_venv.sh`.
- **`tests/`**: `test_basic.py` (imports y existencia de métodos; salta OSM si falta
  `pyrosm`) y `test_functional_gtfs.py` (carga real de un feed GTFS).
- **Config raíz**: `pyproject.toml` (paquete + extras `viz`/`geo`/`api`/`dev`),
  `requirements.txt`, `environment.yml` (conda para libs geoespaciales en Windows),
  `Dockerfile` + `docker-compose.yml`, `tasks.py` (lanzar Jupyter), `.pre-commit-config.yaml` (ruff).

---

## 3. Cómo interactúan los archivos

### 3.1 Ingesta y construcción de índices (`GTFSData`)

`GTFSData("feed.zip")` ejecuta una cadena de pasos en su `__init__`, cada uno
construyendo un índice que el motor consulta en O(1) durante el ruteo:

1. `create_scheduler` → carga el feed con **pygtfs**; si detecta paradas sin
   coordenadas, primero llama a `gtfs_cleaner.clean_gtfs_stops`.
2. `get_gtfs_data` → recorre rutas y trips; construye:
   - `route_stops[route_id][stop_id]` con secuencia, coordenadas, orientación, horarios;
   - un grafo dirigido **rustworkx** por ruta (`graphs`);
   - los índices por trip: `trips` (secuencia de `(stop_id, offset_segundos)`),
     `trip_stop_idx`, `trip_service`, `trip_route`, `trip_dispatches`.
3. `_expand_frequencies` → expande `frequencies.txt`: cada trip frequency-based
   genera **un dispatch por cada salida** dentro de su ventana horaria. (Sin esto el
   motor creería que un bus pasa una sola vez al día → bug de "viajes imposiblemente
   rápidos".)
4. `_build_spatial_index` → **cKDTree** (scipy) sobre las paradas físicas (filtra
   `location_type != 0`) para búsquedas espaciales O(log n).
5. `_build_route_index` → índice invertido `stop→rutas`, paradas ordenadas por
   secuencia, y `trips_here` por `(ruta, parada)` para reconstruir abordajes bajo demanda.
6. `_build_shape_index` → indexa `shapes.txt` para trazar polylines reales por tramo.
7. `_build_route_meta` → `route_type` GTFS → modo canónico (`bus`, `metro`, `rail`, …).
8. `_build_calendar_index` → `calendar.txt` + `calendar_dates.txt` para filtrar
   servicios activos por fecha (`active_services_on`).

### 3.2 Matriz de transferencias (`TransferManager`)

`GTFSData.get_or_compute_transfers(cache_path, osm_graph=...)`:

- Si existe el **cache JSON** y es coherente con el feed (`_validate_transfer_cache`
  exige ≥90% de stop_ids conocidos), lo carga directo.
- Si no, `compute_all_transfers` recorre cada parada de cada ruta, busca rutas
  cercanas (`find_nearby_routes` vía cKDTree) y crea un `TransferConnection` por cada
  par de rutas distintas (top-3 paradas más cercanas). Si hay `OSMGraph`, sustituye la
  distancia Haversine por la **distancia peatonal real**. Persiste el resultado a JSON.

`TransferManager` indexa los transbordos por `(from_route, from_stop)` para consulta
O(1) y deduplica con un `set`. El CSA lo consulta en su *hot path*.

### 3.3 Consulta de un viaje (`ConnectionScanAlgorithm`)

El flujo de `find_journey(origin, destination, departure, profile)` está en §4.

### 3.4 Quién llama a quién

- **`JourneyPlannerV2`** envuelve al CSA con una API simple (`plan_journey`) y reintenta
  con radio de caminata ampliado si no encuentra viaje. Convierte el `Journey` del CSA
  a su formato legacy de `JourneyLeg`.
- **`api/main.py`** instancia `GTFSData` + `TransferManager` (+ `OSMGraph` opcional) en
  el arranque y guarda todo en `STATE`. Cada `/plan` crea un `ConnectionScanAlgorithm`
  nuevo (barato) sobre ese estado y devuelve los `Journey` serializados con `journey_to_dto`.
- **`api/static/app.js`** llama a `/plan` y `/plan/compare`, dibuja segmentos y trazados
  en Leaflet usando los `path` (polylines) que el backend incluye en cada segmento.
- **`visualize.py`** consume los mismos `Journey` para mapas Folium (uso por código/notebooks).

---

## 4. Algoritmos

### 4.1 Connection Scan Algorithm (CSA) — variante multi-target

El núcleo es una **búsqueda de etiquetas tipo Dijkstra** sobre el grafo tiempo-dependiente
de la red de tránsito, ejecutada una vez por cada parada de acceso candidata. Reemplaza
el bucle O×D del CSA canónico por un esquema **one-to-many** (estilo RAPTOR): una sola
corrida desde cada origen alcanza *todos* los destinos candidatos.

**Estado de la cola de prioridad** (min-heap por tiempo de llegada):

```
(arrival_time, counter, stop_id, route_id|None, trip_id|None, num_transfers)
```

El `counter` rompe empates de forma determinística (evita que `heapq` compare `None < str`).
El estado trackea el **trip activo** (no sólo la ruta), lo que permite distinguir:

- **Continuación** — seguir a la siguiente parada del *mismo* bus (no es transbordo,
  sigue el `stop_sequence` real del trip vía `_next_stop_on_trip`).
- **Abordaje** — subir a otra ruta. Si se venía arriba de un bus, cuenta como
  **transbordo** (verifica viabilidad con `TransferManager` y suma un buffer de
  seguridad). El próximo bus se elige con `_next_boarding`, que hace `bisect` sobre los
  abordajes ordenados (expandidos desde `frequencies.txt`) buscando el primero con
  horario ≥ a la hora actual.
- **Footpath** — caminar a una parada cercana distinta; *es* el transbordo, por lo que
  se empuja con `trip=None` para no recontar la próxima subida.

**Concepto clave — "trip virtual" `(trip_id, dispatch_secs)`:** en GTFS frequency-based
el mismo `trip_id` se despacha muchas veces al día, y cada despacho es un bus distinto.
El tiempo absoluto en la parada *k* se reconstruye como `dispatch_secs + offset_secs[k]`.
Esto es lo que evita el bug de viajes imposiblemente rápidos.

**Podas que mantienen la corrida acotada:**
- Horizonte temporal (`time_horizon_hours`): al sacar de la cola algo posterior, se corta.
- Estados ya *settled* `(stop, route)` y pares `(stop, route)` ya visitados.
- Corte temprano cuando ya se alcanzaron todos los destinos.
- Filtro de modo pre-aplicado a `stop→rutas` (no se reevalúa en el hot path).

Complejidad práctica por origen: O(E log V) sobre el subgrafo alcanzable dentro del
horizonte, con E = conexiones (abordajes + continuaciones + footpaths).

### 4.2 Reconstrucción del viaje

`_reconstruct_journey` sigue los punteros inversos `in_connection` desde el destino
hasta el origen y arma la lista de segmentos: caminata de acceso, tramos de tránsito,
transbordos (incluyendo footpaths) y caminata de egreso. Cada tramo a pie pasa por
`_walk`, que pide la **geometría peatonal real a OSM** y cae a Haversine si OSM no está
disponible o devuelve una ruta absurda (>3× la línea recta → snapping a nodo desconectado).

### 4.3 Frente de Pareto 3D

Sobre el conjunto de viajes candidatos (de todos los orígenes, más la caminata directa
si aplica) se aplican, en orden:

1. **Budgets y topes** — descarta los que exceden caminata total o `max_transfers`.
2. **Deduplicación** — `_filter_similar_journeys` colapsa viajes con idéntica secuencia
   de `route_id`.
3. **`_pareto_filter`** — conserva sólo los no dominados en las tres dimensiones
   `(hora_de_llegada, nº_transbordos, distancia_caminada)`. Un viaje domina a otro si es
   ≤ en las tres y < en al menos una. Implementación O(n²) (n suele ser <50).
4. **Diversidad** — si el frente queda más chico que `num_alternatives`,
   `_add_diverse_alternatives` agrega viajes dominados pero con set de rutas distinto.

> Nota: esto **excede la propuesta original**, que prometía un Pareto 2D.

### 4.4 Perfiles de optimización (`_sort_by_profile`)

El frente de Pareto se ordena según el perfil pedido:

| Perfil | Clave de orden |
|---|---|
| `fastest` | `(llegada, transbordos, caminata)` |
| `fewer_transfers` | `(transbordos, llegada, caminata)` |
| `less_walking` | `(caminata, llegada, transbordos)` |
| `balanced` | **costo generalizado** (ver abajo) |
| `prefer_rail` | costo generalizado + sesgo negativo a metro/tren, positivo a bus |

**Costo generalizado** (estilo OpenTripPlanner), aplicado *sólo* en el ranking
post-Pareto (nunca en el eje de tiempo de Dijkstra, que volvería la búsqueda inadmisible):

```
costo = duración_total
      + nº_transbordos × transfer_cost_penalty_seconds   (default 300 s)
      + caminata_km    × 300 s/km                          (≈5 min por km)
      + sesgo_de_modo                                       (penalty por abordaje + peso de duración)
```

### 4.5 Ruteo peatonal sobre OSM (`OSMGraph`)

`OSMGraph` construye un grafo no dirigido **rustworkx** desde el `.pbf` (vía `pyrosm`),
con un **cKDTree** para hallar el nodo más cercano a una coordenada. `shortest_path`
ejecuta **Dijkstra de rustworkx** ponderado por longitud de arista (con cache de pares
`(src, tgt)` hasta 200k entradas) y devuelve `(distancia_km, polyline)`. Es opcional:
todo el sistema degrada a **Haversine** si OSM no está cargado.

### 4.6 Calendario y frecuencias

- **Servicios activos por fecha** (`active_services_on`): aplica rango `[start, end]` y
  día de semana de `calendar.txt`, luego las excepciones de `calendar_dates.txt`
  (1=agrega, 2=quita). Si no hay calendario, el motor cae al pool sin filtrar (robusto
  ante feeds incompletos — nunca devuelve "0 viajes" por esto).
- **Expansión de frecuencias** (`_expand_frequencies`): convierte cada ventana
  `(start, end, headway)` en dispatches discretos cada `headway` segundos.

### 4.7 Estructuras de datos e índices auxiliares

| Estructura | Uso | Costo |
|---|---|---|
| cKDTree espacial (paradas) | `get_nearby_stops`, footpaths | O(log n) |
| cKDTree de nodos OSM | snapping coordenada→nodo | O(log n) |
| `stop→rutas` (invertido) | rutas en una parada | O(1) |
| `trips`, `trip_stop_idx` | siguiente parada del mismo bus | O(1) |
| `trip_dispatches` | abordajes absolutos (frequencies) | construido bajo demanda + cache por request |
| `TransferManager.transfers` | transbordos desde `(ruta, parada)` | O(1) |

---

## 5. Resumen de dependencias entre módulos

```
fetch.py ─────────────► (descarga datasets)
gtfs_cleaner.py ──────► GTFSData (pre-limpieza)
OSMGraph ─────────────► GTFSData (transbordos/shapes) + CSA (_walk)
GTFSData ─────────────► TransferManager, ConnectionScanAlgorithm, JourneyPlannerV2
TransferManager ──────► ConnectionScanAlgorithm (viabilidad + footpaths)
ConnectionScanAlgorithm ─► JourneyPlannerV2, visualize.py, api/main.py
api/schemas.py ───────► api/main.py (DTOs)
api/main.py ──────────► api/static/ (frontend)
```

**Punto de entrada por código:**
```python
from ayatori import GTFSData, ConnectionScanAlgorithm
gtfs = GTFSData("feed.zip")
tm   = gtfs.get_or_compute_transfers(cache_path="cache.json")
csa  = ConnectionScanAlgorithm(gtfs, transfer_manager=tm)
journeys = csa.find_journey(origin, destination, departure, profile="balanced")
```

**Punto de entrada por API:** `docker compose up --build` → `http://localhost:8000`.
