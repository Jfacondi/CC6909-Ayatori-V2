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

> El `~10-30s` del paso 1 corresponde al feed por defecto 2023; con el feed 2026 (`GTFS_20260425_v3`) el parseo del feed toma del orden de minutos (~3–6 min).

## Endpoints

### `GET /health`

```json
{ "status": "ok", "gtfs_loaded": true, "num_routes": 425,
  "num_stops": 12279, "num_transfers": 1462440 }
```

> Cifras con el feed 2026 (`GTFS_20260425_v3`); con el feed por defecto 2023-09-16 son menores (~427 / ~12.211 / ~869 k).

### `GET /geocode?q=`

Proxy a Nominatim (con cache en memoria) para autocompletar direcciones. Devuelve una lista de candidatos `{ display_name, lat, lon }`.

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

Servido desde `/` (estático en `api/static/`). **Sin build step**: `index.html` carga Leaflet desde CDN y la app como ES modules nativos (`<script type="module" src="/static/js/main.js">`). Estructura:

```
api/static/
  index.html            estructura + sprite SVG de iconos
  css/
    tokens.css          design tokens (color, espaciado, tipografía) + temas claro/oscuro
    components.css       botones, chips, cards, toasts, sliders, dropdown…
    layout.css           shell responsive (sidebar ↔ bottom sheet) y vistas
  js/
    main.js              punto de entrada: carga estado y cablea eventos
    api.js               wrappers de fetch a la API
    state.js             estado compartido
    map.js               Leaflet: marcadores, dibujo de viajes, leyenda
    geocoder.js          autocompletado de direcciones (teclado + ARIA)
    results.js           tarjetas, ribbons, badges, timeline, tabla comparativa
    config.js            sliders avanzados, modos, perfiles
    compare.js           variantes del modo comparar
    permalink.js         estado del viaje en la URL (compartir/reproducir)
    format.js, coords.js, dom.js   utilidades puras
    ui/{toast,theme,sheet}.js      componentes de UI
```

Diseño mobile-first y accesible (WCAG AA): navegación por teclado, ARIA, foco visible, `prefers-color-scheme` y `prefers-reduced-motion`.
