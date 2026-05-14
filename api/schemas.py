"""Pydantic schemas para la API Ayatori."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OptimizationProfile = Literal["fastest", "fewer_transfers", "less_walking", "balanced"]
LatLon = tuple[float, float]


class ConfigOverride(BaseModel):
    """Overrides opcionales sobre CSAConfig para una request puntual.

    Sólo los campos enviados sobreescriben el default. Campos desconocidos
    disparan 422 para evitar typos silenciosos.
    """

    model_config = ConfigDict(extra="forbid")

    walking_speed_kmh: float | None = Field(None, ge=1.0, le=10.0)
    max_walking_to_stop_km: float | None = Field(None, ge=0.05, le=5.0)
    max_walking_transfer_km: float | None = Field(None, ge=0.05, le=5.0)
    max_total_walking_km: float | None = Field(None, ge=0.05, le=10.0)
    max_direct_walk_km: float | None = Field(None, ge=0.05, le=10.0)
    max_transfers: int | None = Field(None, ge=0, le=6)
    transfer_buffer_seconds: int | None = Field(None, ge=0, le=1800)
    transfer_cost_penalty_seconds: int | None = Field(None, ge=0, le=3600)
    time_horizon_hours: float | None = Field(None, ge=0.5, le=12.0)
    max_origin_stops: int | None = Field(None, ge=1, le=30)
    max_destination_stops: int | None = Field(None, ge=1, le=30)
    fallback_step_minutes: float | None = Field(None, ge=0.5, le=30.0)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: LatLon = Field(..., description="[lat, lon]")
    destination: LatLon = Field(..., description="[lat, lon]")
    departure: datetime
    num_alternatives: int = Field(3, ge=1, le=10)
    profile: OptimizationProfile = "balanced"
    config: ConfigOverride | None = None


class LabeledConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., min_length=1, max_length=64)
    config: ConfigOverride | None = None
    profile: OptimizationProfile | None = None
    num_alternatives: int | None = Field(None, ge=1, le=10)


class ComparePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: LatLon
    destination: LatLon
    departure: datetime
    variants: list[LabeledConfig] = Field(..., min_length=1, max_length=6)


class SegmentDTO(BaseModel):
    """Representación neutral de un segmento de viaje.

    Mantiene los mismos nombres que el dict que produce CSA, pero con
    duraciones/tiempos serializables.
    """

    type: Literal["walk", "transit", "transfer"]
    duration_seconds: float
    # walk / transit
    from_: str | None = Field(None, alias="from")
    to: str | None = None
    from_latlon: list[float] | None = None
    to_latlon: list[float] | None = None
    distance_km: float | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    # transit
    route_id: str | None = None
    from_stop: str | None = None
    to_stop: str | None = None
    departure_time: datetime | None = None
    arrival_time: datetime | None = None
    # transfer
    from_route: str | None = None
    to_route: str | None = None
    at_stop: str | None = None
    # geometría enriquecida en el handler (no la produce CSA)
    from_coords: list[float] | None = None
    to_coords: list[float] | None = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class JourneyDTO(BaseModel):
    total_duration_seconds: float
    departure_time: datetime
    arrival_time: datetime
    number_of_transfers: int
    total_walking_distance_km: float
    segments: list[SegmentDTO]


class PlanResponse(BaseModel):
    config_used: dict
    profile: OptimizationProfile
    journeys: list[JourneyDTO]


class CompareVariantResponse(BaseModel):
    label: str
    config_used: dict
    profile: OptimizationProfile
    journeys: list[JourneyDTO]


class ComparePlanResponse(BaseModel):
    results: list[CompareVariantResponse]


class NearbyStop(BaseModel):
    stop_id: str
    distance_km: float
    lat: float
    lon: float


class HealthResponse(BaseModel):
    status: str
    gtfs_loaded: bool
    num_routes: int
    num_stops: int
    num_transfers: int


# ──────────────────────────────────────────────────────────────────────
# Serialización Journey (dataclass del dominio) -> JourneyDTO
# ──────────────────────────────────────────────────────────────────────


def _serialize_segment(seg: dict, stop_coords_fn) -> dict:
    """Convierte un segment dict del CSA a uno serializable.

    - timedelta -> duration_seconds
    - datetime queda; Pydantic lo emite ISO 8601
    - enriquece con from_coords/to_coords mirando GTFSData cuando el segmento
      sólo trae stop_ids (útil para que el frontend dibuje sin más queries).
    """
    out: dict[str, Any] = {}
    for k, v in seg.items():
        if isinstance(v, timedelta):
            continue
        out[k] = v

    dur = seg.get("duration")
    out["duration_seconds"] = dur.total_seconds() if isinstance(dur, timedelta) else 0.0

    seg_type = seg.get("type")
    if seg_type == "transit":
        from_stop = seg.get("from_stop")
        to_stop = seg.get("to_stop")
        if from_stop:
            c = stop_coords_fn(from_stop)
            if c is not None:
                lon, lat = c
                out["from_coords"] = [lat, lon]
        if to_stop:
            c = stop_coords_fn(to_stop)
            if c is not None:
                lon, lat = c
                out["to_coords"] = [lat, lon]
    elif seg_type == "transfer":
        at = seg.get("at_stop")
        if at:
            c = stop_coords_fn(at)
            if c is not None:
                lon, lat = c
                out["from_coords"] = [lat, lon]
                out["to_coords"] = [lat, lon]
    elif seg_type == "walk":
        # Si vino con stop_id en from/to (no "origin"/"destination"), resolverlo
        frm = seg.get("from")
        to = seg.get("to")
        if "from_latlon" not in out and frm and frm != "origin":
            c = stop_coords_fn(frm)
            if c is not None:
                lon, lat = c
                out["from_coords"] = [lat, lon]
        if "to_latlon" not in out and to and to != "destination":
            c = stop_coords_fn(to)
            if c is not None:
                lon, lat = c
                out["to_coords"] = [lat, lon]

    return out


def journey_to_dto(journey, stop_coords_fn) -> JourneyDTO:
    return JourneyDTO(
        total_duration_seconds=journey.total_duration.total_seconds(),
        departure_time=journey.departure_time,
        arrival_time=journey.arrival_time,
        number_of_transfers=journey.number_of_transfers,
        total_walking_distance_km=journey.total_walking_distance,
        segments=[
            SegmentDTO.model_validate(_serialize_segment(s, stop_coords_fn))
            for s in journey.segments
        ],
    )
