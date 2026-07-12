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
                          ▼
                     api/main.py
                     (FastAPI /plan, /compare)
                          │
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
│   └── utils/            ← limpieza GTFS y paths
├── api/                  ← capa FastAPI + frontend estático
│   └── static/           ← index.html + js/ (Leaflet, sin build)
├── scripts/              ← setup de entorno (setup_venv.sh)
├── tests/                ← pytest (imports + funcional con GTFS real)
├── notebooks/            ← Jupyter (benchmarks, exploración)
├── examples/             ← ejemplo de uso de la librería
├── docs/                 ← propuesta, entrega preliminar, este documento
├── reports/figures/      ← salidas de gráficos/figuras
├── Dockerfile, docker-compose.yml
├── pyproject.toml, requirements.txt, environment.yml
└── .pre-commit-config.yaml
```

### 2.1 `ayatori/models/` — el corazón del sistema

| Archivo | Responsabilidad | Clases / funciones públicas |
|---|---|---|
| `GTFSData.py` | Carga el feed GTFS y construye **todos los índices** de ruteo. | `GTFSData`, `.get_or_compute_transfers()`, `.get_nearby_stops()`, `.active_services_on()`, `.get_route_shape_segment()` |
| `ConnectionScanAlgorithm.py` | **Motor de ruteo**: Dijkstra multi-target + Pareto + perfiles. | `ConnectionScanAlgorithm`, `CSAConfig`, `Journey` |
| `TransferConnection.py` | Modela un transbordo y administra la matriz precomputada. | `TransferConnection`, `TransferManager` |
| `OSMGraph.py` | Red peatonal real (caminos OSM) para distancias/geometría a pie. | `OSMGraph`, `.shortest_path()`, `.from_file()` |
| `__init__.py` | Reexporta el API público; carga `OSMGraph` de forma opcional. | — |

### 2.2 `ayatori/data/` — adquisición de datos

| Archivo | Función |
|---|---|
| `manifest.toml` | Declara los datasets (GTFS Santiago 2023-09-16, OSM Chile): URL, destino, SHA256. |
| `fetch.py` | CLI `ayatori-fetch-data`: descarga con verificación de hash y extrae ZIPs. |

### 2.3 `ayatori/utils/` — utilidades

| Archivo | Función |
|---|---|
| `gtfs_cleaner.py` | `clean_gtfs_stops()`: descarta paradas sin coordenadas válidas (pathway nodes del feed 2026) y genera un ZIP limpio. Lo invoca `GTFSData.create_scheduler`. |
| `paths.py` | Ruta a la carpeta de datos relativa a la raíz del repo vía `pyprojroot` (`data_dir`). |

### 2.4 `api/` — servicio REST + frontend

| Archivo | Función |
|---|---|
| `main.py` | App FastAPI. En el `lifespan` carga GTFS + (opcional) OSM + matriz de transbordos + shapes sintéticas una sola vez; expone `/health`, `/config/*`, `/stops/nearby`, `/geocode`, `/plan`, `/plan/compare`. |
| `schemas.py` | DTOs Pydantic (`PlanRequest`, `ConfigOverride`, `SegmentDTO`, …) y `journey_to_dto()` que serializa un `Journey` a JSON. |
| `static/index.html` + `js/` | Frontend Leaflet sin build (módulos JS sin bundler): click izq=origen, der=destino, sliders de `CSAConfig`, modo *comparar*. |

### 2.5 Resto

- **`scripts/`**: `setup_venv.sh` (preparación del entorno).
- **`tests/`**: `test_basic.py` (imports y existencia de métodos; salta OSM si falta
  `osmium`), `test_functional_gtfs.py` (carga real de un feed GTFS) y pruebas de
  líneas comunes / presencia de Metro.
- **Config raíz**: `pyproject.toml` (paquete + extras `geo`/`api`/`dev`),
  `requirements.txt`, `environment.yml` (conda para libs geoespaciales en Windows),
  `Dockerfile` + `docker-compose.yml`, `.pre-commit-config.yaml` (ruff).

---

## 3. Cómo interactúan los archivos

### 3.1 Ingesta y construcción de índices (`GTFSData`)

`GTFSData("feed.zip")` ejecuta una cadena de pasos en su `__init__`, cada uno
construyendo un índice que el motor consulta en O(1) durante el ruteo:

1. `create_scheduler` → carga el feed con **pygtfs**; si detecta paradas sin
   coordenadas, primero llama a `gtfs_cleaner.clean_gtfs_stops`.
2. `get_gtfs_data` → recorre rutas y trips; construye:
   - `route_stops[route_id][stop_id]` con secuencia, coordenadas, orientación, horarios;
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

- **`api/main.py`** instancia `GTFSData` + `TransferManager` (+ `OSMGraph` opcional) en
  el arranque y guarda todo en `STATE`. Cada `/plan` crea un `ConnectionScanAlgorithm`
  nuevo (barato) sobre ese estado y devuelve los `Journey` serializados con `journey_to_dto`.
  Si la búsqueda vuelve vacía, reintenta una vez ampliando el presupuesto de caminata.
- **`api/static/js/`** (módulos del frontend) llama a `/plan` y `/plan/compare`, dibuja
  segmentos y trazados en Leaflet usando los `path` (polylines) que el backend incluye en
  cada segmento.

---

## 4. Algoritmos — cómo se implementa cada uno

Esta sección detalla, para cada algoritmo, **dónde vive** (archivo + función),
**qué estructuras de datos** usa y **cómo está implementado** paso a paso. Todo el
comportamiento del motor se parametriza con `CSAConfig` (dataclass en
`ConnectionScanAlgorithm.py`); las perillas más relevantes:

| Campo de `CSAConfig` | Default | Rol |
|---|---|---|
| `walking_speed_kmh` | 5.0 | Velocidad peatón (km/h). |
| `max_walking_to_stop_km` | 1.0 | Budget de caminata acceso/egreso (origen→1ª parada, última→destino). |
| `max_walking_transfer_km` | 0.4 | Budget de caminata por footpath de transbordo. |
| `max_total_walking_km` | 2.0 | Tope global de caminata del viaje. |
| `max_direct_walk_km` | 1.5 | Si origen↔destino ≤ esto, se sugiere caminar directo. |
| `max_transfers` | 3 | Máximo de transbordos. |
| `transfer_buffer_seconds` | 60 | Margen de seguridad real por transbordo (espera mínima). |
| `transfer_cost_penalty_seconds` | 300 | Penalty de **costo** (no de tiempo) por transbordo, sólo en el ranking. |
| `time_horizon_hours` | 3.0 | Horizonte temporal de la búsqueda. |
| `max_origin_stops` / `max_destination_stops` | 8 / 8 | Nº de paradas candidatas de acceso/egreso. |
| `allowed_modes` / `excluded_modes` | None / () | Filtros de modo (consciente de modo). |
| `mode_transfer_penalty_seconds` / `mode_preference_weight` | None | Sesgos de modo (sólo ranking). |
| `use_osm_walking` | True | Usar geometría OSM en los tramos a pie (si hay OSMGraph). |
| `respect_calendar` | True | Filtrar servicios por `calendar.txt`/`calendar_dates.txt`. |

### 4.1 CSA multi-target tipo Dijkstra (núcleo)

- **Qué resuelve:** el viaje más temprano en llegar desde una parada de origen hacia
  **todos** los destinos candidatos en una sola corrida (one-to-many estilo RAPTOR),
  reemplazando el bucle O×D del CSA canónico.
- **Dónde vive:** `ConnectionScanAlgorithm.find_journey` (orquestación) y
  `_connection_scan_multi_target` (el scan).
- **Estructuras de datos:**
  - min-heap `heapq` cuyo **estado** es la tupla
    `(arrival_time, counter, stop_id, route_id|None, trip_id|None, num_transfers)`;
  - `earliest_arrival: dict[stop_id → datetime]` (etiqueta de llegada mínima);
  - `in_connection: dict[stop_id → (from_stop, route_id, dep_dt, arr_dt)]`
    (punteros inversos para reconstruir);
  - `settled_state: set[(stop_id, route_id)]` y `visited_route_at_stop:
    set[(stop_id, route_id)]` (podas);
  - `walk_meta: dict[stop_id → TransferConnection]` (metadata de footpaths);
  - `reached: dict[stop_id → Journey]` (destinos ya resueltos).
- **Cómo se implementa (`find_journey`):**
  1. `_validate_inputs`; calcula servicios activos de la fecha (`active_services_on`).
  2. **Caminata directa:** si la distancia Haversine origen↔destino ≤
     `max_direct_walk_km`, arma un `Journey` sólo-caminata (`_build_direct_walk`); si
     además ≤ `max_walking_to_stop_km`, lo devuelve sin más (política Google/Apple).
  3. Paradas candidatas de acceso y egreso vía `_candidate_stops` (cKDTree).
  4. **Una corrida de `_connection_scan_multi_target` por cada parada de origen**,
     anclando el `start_time = salida + tiempo de caminata de acceso`.
  5. Junta todos los `Journey`, agrega la caminata directa al pool, filtra por
     `max_total_walking_km` y `max_transfers`, deduplica
     (`_filter_similar_journeys`), aplica Pareto (`_pareto_filter`), rellena con
     diversidad si hace falta y ordena por perfil (`_sort_by_profile`).
- **Cómo se implementa (el scan, Dijkstra por etiquetas):** mientras el heap no esté
  vacío se hace `heappop`; si `arrival_time > horizonte` se corta (la cola está
  ordenada por tiempo); se descartan etiquetas obsoletas y estados ya *settled*. Al
  sacar una parada destino, se reconstruye su `Journey` (sin `continue`: la parada
  sigue sirviendo de pivote hacia otros destinos). Luego se **expanden 3 transiciones**:
  - **① Continuación** (`_next_stop_on_trip`): si se viene a bordo de un trip, avanzar
    a la **siguiente parada del mismo bus** siguiendo el `stop_sequence` real. No es
    transbordo y no requiere reabordar.
  - **② Abordaje** (`_next_boarding`): subir a otra ruta en la parada actual. Si se
    venía a bordo de un bus, **cuenta como transbordo** → se valida con
    `_is_transfer_viable` (cacheado) y se suma `transfer_buffer_seconds + 60s` antes
    de poder abordar.
  - **③ Footpath**: caminar a una parada cercana viable (`TransferManager`); *es* el
    transbordo, por lo que se empuja con `trip=None` para que la próxima subida no lo
    recuente.
- **Decisiones clave / complejidad:** el `counter` rompe empates determinísticamente
  (evita que `heapq` compare `None < str`). El estado trackea el **trip activo** (no
  sólo la ruta) → distingue continuar vs. transbordar y evita "Frankensteins" de
  varios trips. Podas: horizonte temporal, estados *settled*, pares `(stop, route)` ya
  visitados, corte temprano al cubrir todos los destinos, y filtro de modo
  pre-aplicado a `stop→rutas`. Complejidad práctica por origen: **O(E log V)** sobre el
  subgrafo alcanzable dentro del horizonte. (El Dijkstra optimiza sólo llegada, así
  que el tope de transbordos se reimpone como filtro post-búsqueda.)

### 4.2 "Trip virtual" y expansión de frecuencias

- **Qué resuelve:** en GTFS *frequency-based* (Santiago) un mismo `trip_id` se
  despacha muchas veces al día; cada despacho es un bus distinto. Modelarlo mal produce
  el bug de "viajes imposiblemente rápidos".
- **Dónde vive:** `GTFSData._expand_frequencies` (precómputo) y el "trip virtual"
  `(trip_id, dispatch_secs)` usado por `_next_boarding`/`_next_stop_on_trip`.
- **Estructuras de datos:** `trips[trip_id] = [(stop_id, offset_secs), ...]` (offsets
  en segundos desde la 1ª parada del trip); `trip_dispatches[trip_id] = [secs, ...]`
  (instantes de salida); `trip_stop_idx[trip_id][stop_id] = posición`.
- **Cómo se implementa:** por cada record `(trip_id, start, end, headway)` de
  `frequencies.txt` se generan despachos con `range(start, end, headway)` y se
  acumulan en `trip_dispatches` (ordenados). Trips sin frecuencia conservan un único
  dispatch = hora absoluta de su primera parada. El **tiempo absoluto** en la parada
  *k* se reconstruye como `dispatch_secs + offset_secs[k]`.
- **Decisiones clave:** guardar offsets+dispatches (no horarios absolutos
  materializados) baja la huella de ~600 MB a ~20 MB; los abordajes absolutos se
  expanden bajo demanda y se cachean por request (§4.3).

### 4.3 Búsqueda binaria del próximo abordaje

- **Qué resuelve:** dado `(ruta, parada, hora)`, hallar en O(log n) el primer bus de
  esa ruta cuyo horario en esa parada sea ≥ a la hora actual.
- **Dónde vive:** `_next_boarding` (búsqueda) y `_active_boardings` (expansión).
- **Estructuras de datos:** lista **ordenada por hora** de
  `(time_of_day, trip_id, dispatch_secs)`; `bisect_left` con `key`; cache
  `active_times_cache[(route, stop, active_services)]` compartida en toda la corrida
  multi-origen.
- **Cómo se implementa:** `_active_boardings` toma `trips_here` de la parada (índice
  ligero `(trip_id, offset_at_stop)`), y por cada trip × cada dispatch calcula
  `abs_secs = dispatch + offset`, lo lleva a hora-del-día (`% 86400`) y arma la lista,
  que ordena por hora. Si hay `active_services`, filtra por `service_id` del trip; si
  la intersección queda vacía, cae al pool sin filtrar (robusto ante calendarios
  incompletos). `_next_boarding` hace `bisect_left` por `time` y devuelve
  `(board_dt, (trip_id, dispatch_secs))`.
- **Decisiones clave:** la expansión es perezosa + cacheada (ver §4.2). El bisect exige
  que el bus realmente **pase por la parada actual** a horario ≥ ahora (no por la
  siguiente), corrigiendo el sesgo de "tomar un bus que ya pasó".

### 4.4 Avance dentro del trip

- **Qué resuelve:** dada una posición en un bus, obtener la siguiente parada y su hora
  real de llegada.
- **Dónde vive:** `_next_stop_on_trip(virtual_trip, current_stop, ref_dt)`.
- **Cómo se implementa:** con `trip_stop_idx` ubica el índice de `current_stop` en la
  secuencia; toma `(next_stop, next_offset)` y calcula
  `next_arr = medianoche(ref) + (dispatch_secs + next_offset)`. Si cae antes de
  `ref_dt`, asume cruce de medianoche y suma un día. Devuelve `None` si la parada es la
  última del trip.

### 4.5 Reconstrucción del viaje y tramos a pie

- **Qué resuelve:** convertir las etiquetas Dijkstra en una lista de segmentos
  (caminata de acceso, tránsito, transbordos/footpaths, caminata de egreso).
- **Dónde vive:** `_reconstruct_journey` y `_walk`.
- **Estructuras de datos:** punteros inversos `in_connection` (por parada) y
  `walk_meta` (footpaths); el `Journey` es un dataclass `slots=True` con
  `segments: list[dict]`.
- **Cómo se implementa:** desde `destination_stop` se sigue `in_connection` hacia atrás
  hasta `origin_stop`, invirtiendo el camino; los tramos con `route_id == "__walk__"`
  se materializan como transbordo a pie (usando el `TransferConnection` de
  `walk_meta`), el resto como tránsito; se anteponen/anexan las caminatas de
  acceso/egreso. Cada tramo a pie pasa por `_walk(from, to, straight_km)`, que pide
  geometría a `OSMGraph.shortest_path` y la adjunta como `seg["path"]`.
- **Decisiones clave:** `_walk` cae a Haversine (sin polyline) si OSM no está
  disponible **o** si la ruta OSM resulta >3× la línea recta (síntoma de snapping a un
  nodo desconectado). Por eso las caminatas siguen calles sólo con OSM activo.

### 4.6 Frente de Pareto 3D (+ dedup + diversidad)

- **Qué resuelve:** quedarse con alternativas genuinamente distintas, no dominadas.
- **Dónde vive:** `_pareto_filter`, `_filter_similar_journeys`,
  `_add_diverse_alternatives`.
- **Cómo se implementa:**
  1. `_filter_similar_journeys` colapsa viajes con idéntica **firma de rutas**
     (`transit_route_signature`).
  2. `_pareto_filter` ordena por `(llegada, transbordos, caminata)` y admite cada
     candidato si no es dominado por uno ya admitido. **A domina a B** si A es ≤ en las
     tres dimensiones `(hora_llegada, nº_transbordos, distancia_caminata)` y < en al
     menos una.
  3. Si el frente queda más chico que `num_alternatives`,
     `_add_diverse_alternatives` agrega viajes dominados pero con **set de rutas
     distinto** (`transit_route_set`).
- **Decisiones clave / complejidad:** O(n²) en el peor caso, pero n suele ser <50.
  Excede la propuesta original (que prometía Pareto 2D).

### 4.7 Perfiles de optimización y costo generalizado

- **Qué resuelve:** ordenar el frente de Pareto según la preferencia del usuario.
- **Dónde vive:** `_sort_by_profile`.
- **Cómo se implementa:**

| Perfil | Clave de orden |
|---|---|
| `fastest` | `(llegada, transbordos, caminata)` |
| `fewer_transfers` | `(transbordos, llegada, caminata)` |
| `less_walking` | `(caminata, llegada, transbordos)` |
| `balanced` | **costo generalizado** (ver abajo) |
| `prefer_rail` | costo generalizado + sesgo (metro/tren favorecidos, bus penalizado) |

  El **costo generalizado** (estilo OpenTripPlanner) es:

```
costo = duración_total
      + nº_transbordos × transfer_cost_penalty_seconds   (default 300 s)
      + caminata_km    × 300 s/km                          (5 min por km)
      + sesgo_de_modo  (Σ penalty por abordaje + Σ duración × (peso_modo − 1))
```

- **Decisión clave:** el costo generalizado y los sesgos de modo se aplican **sólo en
  el ranking post-Pareto**, nunca en el eje de tiempo de Dijkstra — un delta negativo
  en el tiempo volvería la búsqueda inadmisible. `prefer_rail` usa
  `_PREFER_RAIL_TRANSFER_PENALTY` (bus +240, metro −180, rail −240, tram −60 s) si no
  se pasó un mapa de penalties propio.

### 4.8 Precómputo de la matriz de transbordos

- **Qué resuelve:** convertir "¿puedo cambiarme del bus A al B en esta parada?" en una
  consulta O(1) durante la búsqueda.
- **Dónde vive:** `GTFSData.compute_all_transfers` / `get_or_compute_transfers` /
  `_validate_transfer_cache`; `TransferConnection` + `TransferManager`.
- **Estructuras de datos:** `TransferManager.transfers: dict[(from_route, from_stop) →
  list[TransferConnection]]`; `_seen: set[(from_route, from_stop, to_route, to_stop)]`
  para dedup O(1).
- **Cómo se implementa:** por cada `(ruta, parada)`, `find_nearby_routes` (cKDTree)
  trae las rutas vecinas con sus paradas más cercanas; por cada par de rutas distintas
  se crea un `TransferConnection` (con las top-3 paradas más cercanas), usando
  distancia Haversine o **peatonal real (OSM)** si hay grafo. `get_or_compute_transfers`
  carga el cache JSON si `_validate_transfer_cache` lo aprueba (≥90% de stop_ids del
  cache existen en el feed + cobertura por modo) y si no, recomputa y persiste.
- **Decisión clave:** `TransferConnection.is_viable()` exige caminata **≤ 500 m** y
  **≤ 10 min**. La validación por modo evita servir transbordos huérfanos cuando un
  cambio de feed mueve sólo los stop_ids del Metro (≈150 de 12k).

### 4.9 Índices espaciales (cKDTree)

- **Qué resuelve:** paradas/nodos cercanos en O(log n) (acceso/egreso, footpaths,
  snapping coordenada→nodo).
- **Dónde vive:** `GTFSData._build_spatial_index` + `get_nearby_stops` /
  `find_nearby_routes`; `OSMGraph._build_spatial_index` + `find_nearest_node`.
- **Cómo se implementa:** `get_nearby_stops` consulta el cKDTree con
  `query_ball_point` usando un radio en grados (`margin_km/111 × 1.2`), luego **refina
  con Haversine** la distancia exacta de cada candidato, descarta los que exceden el
  margen y ordena por distancia. `find_nearest_node` (OSM) hace `kdtree.query` directo.
- **Decisión clave:** el KDTree usa distancia euclidiana en grados (buena aproximación
  local); el filtro fino Haversine garantiza el radio real en km.

### 4.10 Ruteo peatonal sobre OSM

- **Qué resuelve:** distancia y geometría peatonal real (que sigue calles) entre dos
  coordenadas.
- **Dónde vive:** `OSMGraph.shortest_path` (+ `create_osm_graph`, `find_nearest_node`).
- **Estructuras de datos:** grafo no dirigido `rustworkx.PyGraph` (nodos+aristas de
  `osmium`, peso = longitud en metros), cKDTree de nodos, y cache de pares
  `_sp_cache[(src, tgt)]` (hasta 200k entradas).
- **Cómo se implementa:** snapping de origen/destino al nodo más cercano (KDTree);
  caso `src == tgt` → distancia 0; si no, `rustworkx.dijkstra_shortest_paths`
  ponderado por longitud; reconstruye el polyline `[[lat, lon], ...]` y acumula metros
  por arista; cachea y devuelve `(distancia_km, polyline)`.
- **Decisión clave:** opcional (`AYATORI_USE_OSM=1` + `osmium`); todo el sistema
  degrada a Haversine si no hay OSM o si la ruta es absurda (§4.5).

### 4.11 Calendario: servicios activos por fecha

- **Qué resuelve:** respetar qué servicios operan según la fecha de salida.
- **Dónde vive:** `GTFSData._build_calendar_index` + `active_services_on`.
- **Estructuras de datos:** `services[service_id] = {weekdays(lun..dom), start, end}`;
  `service_exceptions[service_id] = [(date, type)]`; cache `_active_svc_cache[fecha]`.
- **Cómo se implementa:** para la fecha, incluye los `service_id` cuyo rango
  `[start, end]` y día de semana aplican (`calendar.txt`), luego aplica excepciones de
  `calendar_dates.txt` (type 1 = agrega, 2 = quita). Devuelve un `frozenset` cacheado.
- **Decisión clave:** si no hay calendario, devuelve `frozenset()` vacío y el motor cae
  al pool sin filtrar — **nunca** devuelve "0 viajes" por esto.

### 4.12 Recorte de polyline por proyección y shapes sintéticas

- **Qué resuelve:** dibujar el trazado real (shape GTFS) del tramo entre dos paradas.
- **Dónde vive:** `GTFSData.get_route_shape_segment` (+ `get_route_stops_segment` y
  `compute_synthetic_shapes` como fallbacks).
- **Cómo se implementa:** elige la `direction_id` donde `from_stop` precede a `to_stop`,
  toma el shape representativo y proyecta cada parada al vértice más cercano del
  polyline (`argmin` de distancia²), recortando entre ambos índices.
- **Decisión clave — guard contra "forma rara":** en avenidas de doble sentido el
  shape pasa dos veces cerca de una parada y `argmin` puede enganchar la **pasada de
  vuelta**, haciendo que el recorte abarque casi toda la ruta (un tramo de 240 m
  dibujado como 30 km). Si el arco recortado resulta **>4× la distancia recta** entre
  paradas, se descarta el shape y se cae al render lineal del tramo
  (`get_route_stops_segment`). *Mejora futura:* proyección monotónica de todas las
  paradas sobre el shape. Para rutas de bus/tram sin shape GTFS,
  `compute_synthetic_shapes` traza el recorrido por la red vial OSM y lo cachea.

---

## 5. Resumen de dependencias entre módulos

```
fetch.py ─────────────► (descarga datasets)
gtfs_cleaner.py ──────► GTFSData (pre-limpieza)
OSMGraph ─────────────► GTFSData (transbordos/shapes) + CSA (_walk)
GTFSData ─────────────► TransferManager, ConnectionScanAlgorithm
TransferManager ──────► ConnectionScanAlgorithm (viabilidad + footpaths)
ConnectionScanAlgorithm ─► api/main.py
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
