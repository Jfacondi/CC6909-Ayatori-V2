"""Pydantic schemas para la API Ayatori."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ayatori.models.ConnectionScanAlgorithm import KNOWN_MODES

OptimizationProfile = Literal[
    "fastest", "fewer_transfers", "less_walking", "balanced", "prefer_rail"
]
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

    # Consciente de modo
    allowed_modes: list[str] | None = Field(
        None, description="Si se setea, solo estos modos son abordables."
    )
    excluded_modes: list[str] | None = Field(
        None, description="Modos nunca abordables."
    )
    mode_transfer_penalty_seconds: dict[str, int] | None = Field(
        None, description="Sesgo de costo (s) por abordar un modo; negativo=preferir."
    )
    mode_preference_weight: dict[str, float] | None = Field(
        None, description="Multiplicador del tiempo en vehículo por modo."
    )
    mode_access_walk_km: dict[str, float] | None = Field(
        None, description="Budget de caminata de acceso/egreso por modo (km)."
    )

    @field_validator("allowed_modes", "excluded_modes")
    @classmethod
    def _check_mode_list(cls, v):
        if v is not None:
            bad = [m for m in v if m not in KNOWN_MODES]
            if bad:
                raise ValueError(f"modos inválidos: {bad}; válidos: {list(KNOWN_MODES)}")
        return v

    @field_validator(
        "mode_transfer_penalty_seconds",
        "mode_preference_weight",
        "mode_access_walk_km",
    )
    @classmethod
    def _check_mode_map(cls, v):
        if v is not None:
            bad = [m for m in v if m not in KNOWN_MODES]
            if bad:
                raise ValueError(f"modos inválidos: {bad}; válidos: {list(KNOWN_MODES)}")
        return v


def _validate_latlon(v):
    lat, lon = v
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise ValueError("coordenadas fuera de rango (lat∈[-90,90], lon∈[-180,180])")
    return v


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: LatLon = Field(..., description="[lat, lon]")
    destination: LatLon = Field(..., description="[lat, lon]")
    departure: datetime
    num_alternatives: int = Field(3, ge=1, le=10)
    profile: OptimizationProfile = "balanced"
    config: ConfigOverride | None = None

    @field_validator("origin", "destination")
    @classmethod
    def _coords(cls, v):
        return _validate_latlon(v)


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

    @field_validator("origin", "destination")
    @classmethod
    def _coords(cls, v):
        return _validate_latlon(v)


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
    # transit (consciente de modo)
    mode: str | None = None
    route_short_name: str | None = None
    route_long_name: str | None = None
    agency_name: str | None = None
    route_color: str | None = None
    from_stop_name: str | None = None
    to_stop_name: str | None = None
    # transit — líneas comunes: otras líneas que cubren este mismo tramo
    # (mismos paraderos de subida/bajada), como opciones de recorrido.
    route_options: list[dict] | None = None
    # transfer
    from_route: str | None = None
    to_route: str | None = None
    at_stop: str | None = None
    at_stop_name: str | None = None
    from_mode: str | None = None
    to_mode: str | None = None
    # geometría enriquecida en el handler (no la produce CSA)
    from_coords: list[float] | None = None
    to_coords: list[float] | None = None
    # polyline real del shape GTFS para tramos transit ([lat, lon], ...)
    path: list[list[float]] | None = None

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


class GeocodeResult(BaseModel):
    display_name: str
    lat: float
    lon: float
    type: str | None = None


class HealthResponse(BaseModel):
    status: str
    gtfs_loaded: bool
    num_routes: int
    num_stops: int
    num_transfers: int
    # Rango de validez del feed (ISO 'YYYY-MM-DD'); lo consume el frontend para
    # los límites del selector de fecha. None si el feed no trae calendario.
    feed_start: str | None = None
    feed_end: str | None = None


# ──────────────────────────────────────────────────────────────────────
# Serialización Journey (dataclass del dominio) -> JourneyDTO
# ──────────────────────────────────────────────────────────────────────


def _serialize_segment(seg: dict, gtfs) -> dict:
    """Convierte un segment dict del CSA a uno serializable.

    - timedelta -> duration_seconds
    - datetime queda; Pydantic lo emite ISO 8601
    - enriquece con from_coords/to_coords mirando GTFSData cuando el segmento
      sólo trae stop_ids (útil para que el frontend dibuje sin más queries).
    - para segmentos transit, inyecta ``path`` con el polyline real del shape.
    """
    stop_coords_fn = gtfs.get_stop_coords
    stop_name_fn = getattr(gtfs, "get_stop_name", lambda _sid: None)

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
        route_id = seg.get("route_id")
        if from_stop:
            c = stop_coords_fn(from_stop)
            if c is not None:
                lon, lat = c
                out["from_coords"] = [lat, lon]
            out["from_stop_name"] = stop_name_fn(from_stop)
        if to_stop:
            c = stop_coords_fn(to_stop)
            if c is not None:
                lon, lat = c
                out["to_coords"] = [lat, lon]
            out["to_stop_name"] = stop_name_fn(to_stop)
        if route_id and from_stop and to_stop:
            path = gtfs.get_route_shape_segment(route_id, from_stop, to_stop)
            # Fallback para evitar tramos en línea recta cuando el feed no trae
            # shapes para esta ruta: dibujar pasando por las paradas intermedias.
            # Solo aplica a modos que siguen la red vial (no metro, que va por
            # túneles propios y donde la línea recta entre estaciones es
            # razonable).
            if not path and seg.get("mode") != "metro":
                path = gtfs.get_route_stops_segment(route_id, from_stop, to_stop)
            if path:
                out["path"] = path
    elif seg_type == "transfer":
        at = seg.get("at_stop")
        if at:
            out["at_stop_name"] = stop_name_fn(at)
        # Footpath: transbordo caminando entre paradas distintas → resolver
        # ambos extremos y nombres (si no, transbordo en la misma parada).
        fs = seg.get("from_stop")
        tgt = seg.get("to_stop")
        if fs and tgt and fs != tgt:
            cf = stop_coords_fn(fs)
            ct = stop_coords_fn(tgt)
            if cf is not None:
                out["from_coords"] = [cf[1], cf[0]]
            if ct is not None:
                out["to_coords"] = [ct[1], ct[0]]
            out["from_stop_name"] = stop_name_fn(fs)
            out["to_stop_name"] = stop_name_fn(tgt)
        elif at:
            c = stop_coords_fn(at)
            if c is not None:
                out["from_coords"] = [c[1], c[0]]
                out["to_coords"] = [c[1], c[0]]
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


def _common_lines_by_index(segments: list[dict], gtfs) -> dict[int, list[dict]]:
    """Mapea índice de hop transit → líneas comunes de su tramo.

    El CSA emite un segmento ``transit`` por par de paradas consecutivas. Aquí
    agrupamos los hops contiguos de la misma ``route_id`` (un *tramo* completo,
    desde la subida hasta la bajada, sin cruzar transbordos), calculamos las
    líneas que cubren ese mismo tramo (mismos paraderos) vía
    :meth:`GTFSData.common_lines_for_segment`, y asignamos la lista a **cada**
    hop del tramo. Así la consolidación del frontend (que fusiona los hops del
    mismo ``route_id``) conserva ``route_options`` en el segmento resultante.
    """
    fn = getattr(gtfs, "common_lines_for_segment", None)
    if fn is None:
        return {}

    out: dict[int, list[dict]] = {}
    n = len(segments)
    i = 0
    while i < n:
        s = segments[i]
        if s.get("type") != "transit":
            i += 1
            continue
        route_id = s.get("route_id")
        # Tramo = hops contiguos [i, j) con la misma route_id.
        j = i
        leg_stops = [s.get("from_stop")]
        while (
            j < n
            and segments[j].get("type") == "transit"
            and segments[j].get("route_id") == route_id
        ):
            leg_stops.append(segments[j].get("to_stop"))
            j += 1

        if route_id is not None:
            try:
                opts = fn(route_id, leg_stops, s.get("departure_time"))
            except Exception:
                opts = []
            if opts:
                for k in range(i, j):
                    out[k] = opts
        i = j
    return out


def journey_to_dto(journey, gtfs) -> JourneyDTO:
    leg_options = _common_lines_by_index(journey.segments, gtfs)
    seg_dtos: list[SegmentDTO] = []
    for i, s in enumerate(journey.segments):
        out = _serialize_segment(s, gtfs)
        opts = leg_options.get(i)
        if opts:
            out["route_options"] = opts
        seg_dtos.append(SegmentDTO.model_validate(out))
    return JourneyDTO(
        total_duration_seconds=journey.total_duration.total_seconds(),
        departure_time=journey.departure_time,
        arrival_time=journey.arrival_time,
        number_of_transfers=journey.number_of_transfers,
        total_walking_distance_km=journey.total_walking_distance,
        segments=seg_dtos,
    )
