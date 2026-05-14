# Ayatori API

Capa FastAPI sobre el motor `ConnectionScanAlgorithm`. Carga GTFS + transferencias una sola vez al arranque (`lifespan`) y atiende múltiples requests reutilizando el estado en memoria.

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `AYATORI_GTFS_PATH` | `ayatori/data/GTFS/2023-09-16/GTFS-V100-PO20230916.zip` | Ruta al feed GTFS (ZIP o directorio) |
| `AYATORI_TRANSFERS_CACHE` | `ayatori/data/cache/transfers.json` | Cache JSON de transferencias precomputadas |
| `AYATORI_TRANSFERS_MAX_DIST_KM` | `0.5` | Radio máximo (km) al precomputar transferencias |

## Ciclo de vida

```
┌─ startup ─────────────────────────────────────────────────────────────┐
│  1. GTFSData(GTFS_PATH)                          ~10-30s              │
│  2. get_or_compute_transfers(cache=...)          minutos si no cache  │
│  3. State global: { gtfs, transfer_manager }                          │
└────────────────────────────────────────────────────────────────────────┘

┌─ por request (POST /plan) ────────────────────────────────────────────┐
│  1. Construir CSAConfig (merge default + override)                    │
│  2. Instanciar ConnectionScanAlgorithm                  µs            │
│  3. csa.find_journey(...)                                ms-segundos  │
│  4. Serializar Journey -> JourneyDTO                                   │
└────────────────────────────────────────────────────────────────────────┘
```

## Endpoints

### `GET /health`

```json
{ "status": "ok", "gtfs_loaded": true, "num_routes": 427,
  "num_stops": 12211, "num_transfers": 869470 }
```

### `GET /config/defaults`

Devuelve `CSAConfig()` serializado. Útil para inicializar formularios.

### `GET /config/schema`

Por cada campo de `CSAConfig`: `{default, min, max, step}`. Pensado para sliders.

### `GET /stops/nearby?lat=&lon=&radius_km=&max_stops=`

```json
[ { "stop_id": "PA433", "distance_km": 0.082, "lat": -33.437, "lon": -70.651 }, ... ]
```

### `POST /plan`

Request:
```json
{
  "origin": [-33.4372, -70.6506],
  "destination": [-33.4489, -70.6693],
  "departure": "2023-09-04T08:00:00",
  "num_alternatives": 5,
  "profile": "balanced",
  "config": {
    "max_walking_to_stop_km": 1.2,
    "max_transfers": 2
  }
}
```

`config` es opcional; sólo los campos enviados sobreescriben los defaults. Campos desconocidos disparan **422** (`extra="forbid"`).

Response: lista de viajes Pareto-óptimos con segmentos `walk` / `transit` / `transfer` (ver `api/schemas.py::JourneyDTO`).

### `POST /plan/compare`

Ejecuta varias configuraciones en una sola request. Pensado para A/B visual:

```json
{
  "origin": [-33.4372, -70.6506],
  "destination": [-33.4489, -70.6693],
  "departure": "2023-09-04T08:00:00",
  "variants": [
    { "label": "Conservador", "config": { "max_walking_to_stop_km": 0.5 } },
    { "label": "Permisivo",   "config": { "max_walking_to_stop_km": 1.5 } }
  ]
}
```

Cada variante puede override su propio `profile` y `num_alternatives`. Devuelve `{ results: [{ label, journeys, config_used, profile }] }`.

## Swagger

FastAPI genera documentación interactiva en `/docs`. Probar `POST /plan` desde ahí no requiere escribir cURL.

## Frontend

Servido desde `/` (estático en `api/static/`). No tiene build step — `index.html` carga Leaflet desde CDN y `/static/app.js` directo. Para modificar la UI, editar esos dos archivos.
