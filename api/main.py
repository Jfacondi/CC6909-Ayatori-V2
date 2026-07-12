"""FastAPI app for Ayatori.

Loads GTFS + TransferManager once at startup; subsequent requests reuse the
in-memory state and build a fresh ConnectionScanAlgorithm per call (cheap —
it only wires references to the shared GTFSData and TransferManager).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

# Carga variables desde .env antes de leerlas con os.environ.get. Las que ya
# estén en el entorno (shell, Docker) tienen prioridad.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ayatori.models import GTFSData
from ayatori.models.ConnectionScanAlgorithm import (
    ConnectionScanAlgorithm,
    CSAConfig,
)

from .schemas import (
    ComparePlanRequest,
    ComparePlanResponse,
    CompareVariantResponse,
    ConfigOverride,
    GeocodeResult,
    HealthResponse,
    NearbyStop,
    PlanRequest,
    PlanResponse,
    journey_to_dto,
)

logger = logging.getLogger("ayatori.api")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")

# Resolved relative to repo root (working dir at runtime).
DEFAULT_GTFS = os.environ.get(
    "AYATORI_GTFS_PATH",
    "ayatori/data/GTFS/2023-09-16/GTFS-V100-PO20230916.zip",
)
DEFAULT_TRANSFERS_CACHE = os.environ.get(
    "AYATORI_TRANSFERS_CACHE",
    "ayatori/data/cache/transfers.json",
)
TRANSFERS_MAX_DISTANCE_KM = float(os.environ.get("AYATORI_TRANSFERS_MAX_DIST_KM", "0.5"))

# Shapes sintéticas: cuando el GTFS no trae shape para una ruta de bus/tram,
# se traza pasando por la red vial OSM. Cacheado en disco. Sólo se activa si
# hay OSM cargado (sin OSM, la API cae al fallback "polyline por paradas").
DEFAULT_SYNTHETIC_SHAPES_CACHE = os.environ.get(
    "AYATORI_SYNTHETIC_SHAPES_CACHE",
    "ayatori/data/cache/synthetic_shapes_osm.json",
)
USE_SYNTHETIC_SHAPES = os.environ.get("AYATORI_SYNTHETIC_SHAPES", "1") == "1"

# Ruteo peatonal real con OSM. Opt-in: requiere el extra `geo` (pyrosm) y un
# .pbf. Si falla la carga, se degrada a Haversine sin error.
USE_OSM = os.environ.get("AYATORI_USE_OSM", "0") == "1"
DEFAULT_OSM_PBF = os.environ.get("AYATORI_OSM_PBF", "ayatori/data/OSM/Santiago.osm.pbf")

STATE: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    gtfs_path = Path(DEFAULT_GTFS)
    if not gtfs_path.exists():
        raise RuntimeError(
            f"GTFS file not found at {gtfs_path.resolve()}.\n"
            f"  - Run `ayatori-fetch-data` to download it (see ayatori/data/manifest.toml), or\n"
            f"  - Set AYATORI_GTFS_PATH to point to an existing feed."
        )

    logger.info("Loading GTFS from %s", gtfs_path)
    gtfs = GTFSData(str(gtfs_path))
    logger.info("GTFS loaded: %d routes, %d stops", len(gtfs.route_stops), len(gtfs.stops))

    # ── Carga opcional del grafo peatonal OSM (degradación elegante) ──────────
    osm = None
    if USE_OSM:
        pbf = Path(DEFAULT_OSM_PBF)
        if not pbf.exists():
            logger.warning("AYATORI_USE_OSM=1 pero no existe %s; usando Haversine", pbf)
        else:
            try:
                import time as _t

                from ayatori.models.OSMGraph import OSMGraph

                t0 = _t.time()
                logger.info("Loading OSM pedestrian graph from %s ...", pbf)
                osm = OSMGraph(str(pbf))
                logger.info("OSM graph loaded in %.1fs", _t.time() - t0)
            except Exception as e:  # pyrosm ausente / pbf inválido / etc.
                logger.warning("OSM no disponible (%s); usando Haversine", e)
                osm = None

    # Cache de transbordos separada por modo para no colisionar Haversine/OSM.
    base_cache = Path(DEFAULT_TRANSFERS_CACHE)
    if osm is not None:
        cache_path = base_cache.with_name(base_cache.stem + "_osm" + base_cache.suffix)
    else:
        cache_path = base_cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Loading/computing transfer matrix (cache=%s)", cache_path)
    tm = gtfs.get_or_compute_transfers(
        cache_path=str(cache_path),
        max_distance_km=TRANSFERS_MAX_DISTANCE_KM,
        osm_graph=osm,
    )
    logger.info("Transfers ready: %d entries", tm.count_transfers())

    # ── Shapes sintéticas para rutas sin shape GTFS (requiere OSM) ──────────
    if USE_SYNTHETIC_SHAPES and osm is not None:
        synth_cache = Path(DEFAULT_SYNTHETIC_SHAPES_CACHE)
        synth_cache.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Loading/computing synthetic shapes (cache=%s)", synth_cache)
        gtfs.get_or_compute_synthetic_shapes(
            cache_path=str(synth_cache), osm_graph=osm
        )
        logger.info("Synthetic shapes ready")
    elif USE_SYNTHETIC_SHAPES:
        logger.info(
            "Synthetic shapes habilitadas pero OSM no está disponible; "
            "se usará fallback 'polyline por paradas'."
        )

    STATE["gtfs"] = gtfs
    STATE["tm"] = tm
    STATE["osm"] = osm
    STATE["num_transfers"] = tm.count_transfers()
    STATE["feed_start"], STATE["feed_end"] = gtfs.feed_date_range()
    yield
    STATE.clear()


app = FastAPI(title="Ayatori API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────


def _require_state():
    if "gtfs" not in STATE:
        raise HTTPException(status_code=503, detail="GTFS data not loaded yet")


def _config_from_override(override: ConfigOverride | None) -> CSAConfig:
    base = CSAConfig()
    if override is None:
        return base
    overrides = override.model_dump(exclude_unset=True, exclude_none=True)
    # CSAConfig.allowed_modes/excluded_modes son tuple; Pydantic entrega list.
    for key in ("allowed_modes", "excluded_modes"):
        if key in overrides and overrides[key] is not None:
            overrides[key] = tuple(overrides[key])
    return replace(base, **overrides)


def _stop_signature(journey) -> tuple:
    """Firma de un viaje por sus PARADEROS (ignora qué línea se tomó).

    Dos viajes con la misma secuencia de tramos ``(paradero_subida,
    paradero_bajada)`` y la misma estructura de caminata/transbordo son "el
    mismo trayecto": sólo difieren en la línea elegida, que el enriquecimiento de
    líneas comunes ya expone como opciones del tramo. Sirve para colapsarlos.
    """
    sig: list = []
    for s in journey.segments:
        t = s.get("type")
        if t == "transit":
            sig.append(("T", s.get("from_stop"), s.get("to_stop")))
        elif t == "transfer":
            sig.append(("X", s.get("from_stop") or s.get("at_stop"),
                        s.get("to_stop") or s.get("at_stop")))
        elif t == "walk":
            sig.append(("W", s.get("from"), s.get("to")))
    return tuple(sig)


def _collapse_equivalent_journeys(journeys: list) -> list:
    """Colapsa viajes con idéntica firma de paraderos, conservando el de llegada
    más temprana. Preserva el orden de aparición del primer representante."""
    rep: dict[tuple, Any] = {}
    order: list[tuple] = []
    for j in journeys:
        sig = _stop_signature(j)
        prev = rep.get(sig)
        if prev is None:
            rep[sig] = j
            order.append(sig)
        elif j.arrival_time < prev.arrival_time:
            rep[sig] = j
    return [rep[sig] for sig in order]


def _run_plan(req_origin, req_destination, req_departure, profile, num_alternatives, csa_config):
    gtfs = STATE["gtfs"]
    tm = STATE["tm"]

    def _search(cfg):
        csa = ConnectionScanAlgorithm(
            gtfs, transfer_manager=tm, config=cfg, osm_graph=STATE.get("osm")
        )
        return csa.find_journey(
            origin_coords=tuple(req_origin),
            destination_coords=tuple(req_destination),
            departure_time=req_departure,
            num_alternatives=num_alternatives,
            profile=profile,
        )

    journeys = _search(csa_config)
    if not journeys:
        # Fallback: una parada en zona de baja densidad puede quedar apenas
        # fuera del presupuesto de caminata. Reintentar una vez ampliándolo
        # antes de devolver "sin viajes".
        widened = replace(
            csa_config,
            max_walking_to_stop_km=min(csa_config.max_walking_to_stop_km * 2, 5.0),
            max_total_walking_km=min(
                max(csa_config.max_total_walking_km, csa_config.max_walking_to_stop_km * 2 + 1.0),
                10.0,
            ),
        )
        journeys = _search(widened)
    # "Mismo trayecto, distinta línea" → un solo viaje; las líneas alternativas
    # del tramo se exponen como route_options en journey_to_dto.
    journeys = _collapse_equivalent_journeys(journeys)
    return [journey_to_dto(j, gtfs) for j in journeys]


# ──────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────


@app.get("/health", response_model=HealthResponse)
def health():
    gtfs = STATE.get("gtfs")
    return HealthResponse(
        status="ok" if gtfs is not None else "loading",
        gtfs_loaded=gtfs is not None,
        num_routes=len(gtfs.route_stops) if gtfs else 0,
        num_stops=len(gtfs.stops) if gtfs else 0,
        num_transfers=STATE.get("num_transfers", 0),
        feed_start=STATE.get("feed_start"),
        feed_end=STATE.get("feed_end"),
    )


@app.get("/config/schema")
def config_schema():
    """Rangos sugeridos para sliders del frontend (no es JSON-Schema completo,
    es metadata pragmática)."""
    defaults = asdict(CSAConfig())
    bounds = {
        "walking_speed_kmh": {"min": 1.0, "max": 10.0, "step": 0.1},
        "max_walking_to_stop_km": {"min": 0.05, "max": 5.0, "step": 0.05},
        "max_walking_transfer_km": {"min": 0.05, "max": 5.0, "step": 0.05},
        "max_total_walking_km": {"min": 0.05, "max": 10.0, "step": 0.1},
        "max_direct_walk_km": {"min": 0.05, "max": 10.0, "step": 0.1},
        "max_transfers": {"min": 0, "max": 6, "step": 1},
        "transfer_buffer_seconds": {"min": 0, "max": 1800, "step": 30},
        "transfer_cost_penalty_seconds": {"min": 0, "max": 3600, "step": 30},
        "time_horizon_hours": {"min": 0.5, "max": 12.0, "step": 0.5},
        "max_origin_stops": {"min": 1, "max": 30, "step": 1},
        "max_destination_stops": {"min": 1, "max": 30, "step": 1},
    }
    from .schemas import KNOWN_MODES

    # Sólo ofrecer modos que existen en el feed cargado; si el GTFS aún no está
    # disponible, caer a la lista canónica completa.
    gtfs = STATE.get("gtfs")
    mode_options = gtfs.available_modes() if gtfs is not None else list(KNOWN_MODES)

    # El frontend (config.js) sólo lee min/max/step/default por slider y
    # allowed_modes.options; no emitimos metadata que nadie consume.
    out: dict = {name: {"default": defaults[name], **b} for name, b in bounds.items()}
    out["allowed_modes"] = {"default": defaults["allowed_modes"], "options": list(mode_options)}
    return out


@app.get("/stops/nearby", response_model=list[NearbyStop])
def stops_nearby(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius_km: float = Query(0.5, gt=0, le=5.0),
    max_stops: int = Query(20, ge=1, le=100),
):
    _require_state()
    gtfs = STATE["gtfs"]
    pairs = gtfs.get_nearby_stops((lat, lon), margin_km=radius_km, max_stops=max_stops)
    out: list[NearbyStop] = []
    for stop_id, dist in pairs:
        coords = gtfs.get_stop_coords(stop_id)
        if coords is None:
            continue
        clon, clat = coords
        out.append(NearbyStop(stop_id=stop_id, distance_km=dist, lat=clat, lon=clon))
    return out


# ──────────────────────────────────────────────────────────────────────
# Geocoding (proxy a Nominatim con cache en memoria)
#
# Política de Nominatim: User-Agent identificable, máximo 1 req/s,
# preferir cache. Aquí cacheamos cada (q, limit, countrycodes) sin TTL
# (las direcciones no se mueven). countrycodes=cl evita matches en otros
# países dado que el feed GTFS solo cubre Santiago.
# ──────────────────────────────────────────────────────────────────────

_GEOCODE_USER_AGENT = "Ayatori/0.2.0 (FCFM memoria de titulo)"
_GEOCODE_URL = "https://nominatim.openstreetmap.org/search"
_geocode_cache: dict[tuple[str, int, str], list[GeocodeResult]] = {}


@app.get("/geocode", response_model=list[GeocodeResult])
async def geocode(
    q: str = Query(..., min_length=2, max_length=200),
    limit: int = Query(5, ge=1, le=10),
    countrycodes: str = Query("cl", min_length=2, max_length=20),
):
    import httpx

    key = (q.strip().lower(), limit, countrycodes.lower())
    cached = _geocode_cache.get(key)
    if cached is not None:
        return cached

    params = {
        "q": q,
        "format": "json",
        "limit": limit,
        "countrycodes": countrycodes,
    }
    headers = {"User-Agent": _GEOCODE_USER_AGENT, "Accept-Language": "es"}
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(_GEOCODE_URL, params=params, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        raise HTTPException(
            status_code=503, detail=f"Geocoder no disponible: {e!s}"
        ) from e

    out = [
        GeocodeResult(
            display_name=d["display_name"],
            lat=float(d["lat"]),
            lon=float(d["lon"]),
            type=d.get("type"),
        )
        for d in data
    ]
    _geocode_cache[key] = out
    return out


@app.post("/plan", response_model=PlanResponse)
def plan(req: PlanRequest):
    _require_state()
    cfg = _config_from_override(req.config)
    dtos = _run_plan(
        req.origin,
        req.destination,
        req.departure,
        req.profile,
        req.num_alternatives,
        cfg,
    )
    return PlanResponse(
        config_used=asdict(cfg),
        profile=req.profile,
        journeys=dtos,
    )


@app.post("/plan/compare", response_model=ComparePlanResponse)
def plan_compare(req: ComparePlanRequest):
    _require_state()
    results: list[CompareVariantResponse] = []
    for variant in req.variants:
        cfg = _config_from_override(variant.config)
        profile = variant.profile or "balanced"
        n = variant.num_alternatives or 3
        dtos = _run_plan(
            req.origin,
            req.destination,
            req.departure,
            profile,
            n,
            cfg,
        )
        results.append(
            CompareVariantResponse(
                label=variant.label,
                config_used=asdict(cfg),
                profile=profile,
                journeys=dtos,
            )
        )
    return ComparePlanResponse(results=results)


# ──────────────────────────────────────────────────────────────────────
# Static frontend (mounted last so /plan etc. take precedence)
# ──────────────────────────────────────────────────────────────────────

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

    @app.get("/")
    def index():
        return FileResponse(_static_dir / "index.html")
