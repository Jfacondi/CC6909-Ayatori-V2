# TODO

## Roadmap

### Fase 1: Base multimodal
- [x] 1. Unificar GTFS y OSM en una capa de rutas coherente. (`MultimodalLayer`)
- [x] 2. Confirmar y endurecer `TransferManager` para precalcular transferencias viables.
- [x] 3. Revisar el manejo de errores en `GTFSData` y `JourneyPlannerV2`.

### Fase 2: Motor de ruteo
- [x] 4. Completar el CSA con relajacion de transbordos.
- [x] 5. Implementar salida Pareto para tiempo total y numero de transbordos.
- [x] 6. Verificar que el planificador principal no dependa del fallback simplificado.

### Fase 3: Presentacion y validacion
- [x] 7. Implementar la visualizacion formal de rutas y tramos.
- [x] 8. Probar el flujo con casos reales y comparar resultados con referencias manuales.
- [x] 9. Consolidar scripts de demo y tests end-to-end.

---

## Avances

### Migración de grafos a rustworkx
- Reemplazado NetworkX por `rustworkx` (0.17.1) en `OSMGraph.py` y `GTFSData.py` para mayor eficiencia.
- `OSMGraph` ahora usa `rx.PyGraph` con mapeos bidireccionales `_node_id_to_idx` / `_idx_to_node_id`.
- `GTFSData` crea un `rx.PyDiGraph` por ruta con mapeos `node_map` / `idx_to_node` locales por ruta.
- Tests de rustworkx añadidos en `tests/test_basic.py` (`TestRustworkxUsage`).

### Fase 1 — Base multimodal
- **`MultimodalLayer`** (`ayatori/models/MultimodalLayer.py`): nueva clase que unifica GTFS y OSM en una sola interfaz. Calcula tiempos de caminata usando la red OSM real (Dijkstra) o haversine como fallback.
- **`TransferManager` endurecido**: deduplicación O(1) con `_seen: set`, persistencia JSON con `save(path)` / `load(path)`, y método `get_or_compute_transfers()` en `GTFSData` para caché automático.
- **`GTFSData` — índices de rendimiento**: añadido `_build_route_index()` que construye `_stop_to_routes` (índice invertido O(1) parada→rutas) y `_sorted_route_stops` (paradas pre-ordenadas por secuencia para bisect).
- **Corrección `get_routes_at_stop`**: la lógica anterior comparaba una parada consigo misma; reemplazada por consulta directa al índice.
- **Corrección `get_near_stop_ids`**: reemplazada búsqueda O(R×S) por `cKDTree` vía `get_nearby_stops()`.
- **`JourneyPlannerV2` — bugs corregidos**:
  - `stop_coords` siempre vacío → ahora usa `get_stop_coords()` con flip lat/lon correcto.
  - `_estimate_transit_time` pasaba `date` object a `strptime` → ahora usa `strftime("%d/%m/%Y")`.
  - Alias `find_nearby_destination_stops` eliminado; se llama directamente `find_nearby_origin_stops`.

### Optimización de ConnectionScanAlgorithm
- Eliminadas estructuras obsoletas (`class Connection`, `_connections_cache`, `_connections_by_stop`).
- `_get_routes_at_stop()` pasa de O(R×S) a O(1) usando `_stop_to_routes`.
- `_get_next_stops_on_route()` usa `bisect.bisect_right` sobre paradas pre-ordenadas para evitar recorrer las anteriores.
- `__init__` reutiliza `_stop_to_routes` de GTFSData si ya está construido.

### Limpieza general del repositorio
- Eliminados 110 archivos `Zone.Identifier` (metadatos NTFS de Windows).
- Eliminadas importaciones muertas en `JourneyPlanner.py`, `connection_scan_beta.py`, `pbf_testing.py`.
- Flechas Unicode `→` en `__repr__` reemplazadas por `->` para compatibilidad con terminales Windows (cp1252).

### Tests y verificación en runtime
- Tests de OSMGraph marcados con `@pytest.mark.skipif` cuando `pyrosm` no está disponible (requiere conda en Windows).
- Verificación end-to-end con `santiago-gtfs.zip`: GTFSData (427 rutas, 12.211 paradas), CSA (3 viajes encontrados), TransferManager (869.470 transferencias), JourneyPlannerV2 (viaje 23 min, 2 transbordos).

### Pareto, fallback y visualización
- **Frente de Pareto** (`_pareto_filter` en `ConnectionScanAlgorithm`): filtra el conjunto de viajes retornando solo los no dominados — un viaje A se excluye si existe otro B con igual o menor duración Y igual o menor número de transbordos, siendo estrictamente mejor en al menos una dimensión.
- **Sin dependencia del fallback** (`JourneyPlannerV2`): el path CSA ahora reintenta con radio de caminata 2× antes de renunciar, y retorna `None` en lugar de delegar silenciosamente al planificador simplificado. `_find_routes_at_stop` reemplazado por consulta O(1) al índice `_stop_to_routes`.
- **Visualización formal** (`ayatori/visualization/visualize.py`): tres funciones nuevas — `visualize_journey` (dibuja un viaje completo con segmentos walk/transit/transfer diferenciados por color y estilo), `visualize_routes` (dibuja rutas GTFS por ID), `visualize_stops` (paradas cercanas a un punto). Todas retornan un objeto `folium.Map` y aceptan `output_path` para guardar HTML.
