"""FastAPI app for Ayatori.

Loads GTFS + TransferManager once at startup; subsequent requests reuse the
in-memory state and build a fresh ConnectionScanAlgorithm per call (cheap —
it only wires references to the shared GTFSData and TransferManager).
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, fields, replace
from pathlib import Path
from typing import Any

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

    cache_path = Path(DEFAULT_TRANSFERS_CACHE)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("Loading/computing transfer matrix (cache=%s)", cache_path)
    tm = gtfs.get_or_compute_transfers(
        cache_path=str(cache_path),
        max_distance_km=TRANSFERS_MAX_DISTANCE_KM,
    )
    logger.info("Transfers ready: %d entries", tm.count_transfers())

    STATE["gtfs"] = gtfs
    STATE["tm"] = tm
    STATE["num_transfers"] = tm.count_transfers()
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
    return replace(base, **overrides)


def _run_plan(req_origin, req_destination, req_departure, profile, num_alternatives, csa_config):
    gtfs = STATE["gtfs"]
    tm = STATE["tm"]
    csa = ConnectionScanAlgorithm(gtfs, transfer_manager=tm, config=csa_config)
    journeys = csa.find_journey(
        origin_coords=tuple(req_origin),
        destination_coords=tuple(req_destination),
        departure_time=req_departure,
        num_alternatives=num_alternatives,
        profile=profile,
    )
    return [journey_to_dto(j, gtfs.get_stop_coords) for j in journeys]


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
    )


@app.get("/config/defaults")
def config_defaults():
    return asdict(CSAConfig())


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
        "fallback_step_minutes": {"min": 0.5, "max": 30.0, "step": 0.5},
    }
    return {
        f.name: {"default": defaults[f.name], **bounds.get(f.name, {})} for f in fields(CSAConfig())
    }


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
