"""
Connection Scan Algorithm con soporte para transbordos.

Implementación alineada con prácticas estándar de planificadores de transporte
(OpenTripPlanner / RAPTOR / Google Maps):

- Costo generalizado: arrival_time + transfer_cost_penalty × #transbordos.
- Buffer de seguridad por transbordo (separado del costo).
- Budgets de caminata por tramo: acceso/egreso, transbordo, y total.
- Búsqueda multi-target: una sola corrida desde cada origen alcanza todos los
  destinos candidatos (equivalente práctico al one-to-many de RAPTOR; reemplaza
  el bucle O × D del anterior diseño).
- Frente de Pareto sobre (llegada, #transbordos, caminata).
- Perfiles de optimización: fastest, fewer_transfers, less_walking, balanced.
"""

import heapq
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import asin, cos, radians, sin, sqrt
from typing import Literal

_EARTH_RADIUS_KM = 6371.0

OptimizationProfile = Literal["fastest", "fewer_transfers", "less_walking", "balanced"]


@dataclass
class CSAConfig:
    """Parámetros de comportamiento del planificador.

    Los defaults siguen convenciones estándar de planificadores urbanos:
    OTP, RAPTOR, Google Maps.
    """

    # Velocidades / distancias
    walking_speed_kmh: float = 5.0  # 1.4 m/s — estándar peatón urbano

    # Budgets de caminata por tramo (km) — Google/Citymapper-style
    max_walking_to_stop_km: float = 1.0  # origen → primera parada y última parada → destino
    max_walking_transfer_km: float = 0.4  # entre paradas al hacer transbordo
    max_total_walking_km: float = 2.0  # tope global del viaje
    max_direct_walk_km: float = 1.5  # si origen ↔ destino ≤ esto, sugerir caminar directo

    # Transbordos
    max_transfers: int = 3
    transfer_buffer_seconds: int = 60  # margen real de seguridad (espera mínima)
    transfer_cost_penalty_seconds: int = (
        300  # penalty equivalente en costo (no en tiempo) por transbordo (OTP default ~600)
    )

    # Búsqueda
    time_horizon_hours: float = 3.0
    max_origin_stops: int = 8
    max_destination_stops: int = 8
    fallback_step_minutes: float = 2.0  # estimación cuando no hay horario


@dataclass(slots=True)
class Journey:
    """Viaje completo con múltiples segmentos."""

    segments: list[dict]
    total_duration: timedelta
    departure_time: datetime
    arrival_time: datetime
    number_of_transfers: int
    total_walking_distance: float  # en km

    def __lt__(self, other: "Journey") -> bool:
        """Orden lexicográfico (llegada, transbordos, caminata)."""
        return (self.arrival_time, self.number_of_transfers, self.total_walking_distance) < (
            other.arrival_time,
            other.number_of_transfers,
            other.total_walking_distance,
        )

    def __repr__(self) -> str:
        hours = self.total_duration.total_seconds() / 3600
        return (
            f"Journey(duration={hours:.1f}h, transfers={self.number_of_transfers}, "
            f"walk={self.total_walking_distance:.2f}km)"
        )

    def transit_route_signature(self) -> tuple[str, ...]:
        """Secuencia de route_ids del tránsito — sirve como clave de deduplicación."""
        return tuple(s["route_id"] for s in self.segments if s["type"] == "transit")

    def transit_route_set(self) -> frozenset[str]:
        return frozenset(self.transit_route_signature())


class ConnectionScanAlgorithm:
    """
    CSA orientado a planificación urbana multimodal.

    Soporta:
    - Caminata acceso/egreso/transbordo con budgets separados.
    - Costo generalizado con penalty por transbordo.
    - Búsqueda multi-target (una corrida por origen, todos los destinos).
    - Pareto 3D + perfiles.
    """

    def __init__(
        self,
        gtfs_data,
        transfer_manager=None,
        config: CSAConfig | None = None,
        # — compatibilidad hacia atrás —
        max_walking_distance_km: float | None = None,
        walking_speed_kmh: float | None = None,
        max_transfers: int | None = None,
    ):
        self.gtfs = gtfs_data
        self.transfer_manager = transfer_manager
        self.config = config or CSAConfig()

        # Si llegan kwargs legacy, sobreescriben el default del config.
        if max_walking_distance_km is not None:
            self.config.max_walking_to_stop_km = max_walking_distance_km
        if walking_speed_kmh is not None:
            self.config.walking_speed_kmh = walking_speed_kmh
        if max_transfers is not None:
            self.config.max_transfers = max_transfers

        # Aliases convenientes
        self.walking_speed = self.config.walking_speed_kmh
        self.max_walking_km = self.config.max_walking_to_stop_km  # compat externo
        self.max_transfers = self.config.max_transfers

        # Índice stop→rutas: usa el de GTFSData si ya está construido; lo reconstruye si no
        self._stop_to_routes: dict = getattr(gtfs_data, "_stop_to_routes", None)
        if self._stop_to_routes is None:
            self._stop_to_routes = defaultdict(list)
            for route_id, stops_dict in gtfs_data.route_stops.items():
                for sid in stops_dict:
                    self._stop_to_routes[sid].append(route_id)

    # ────────────────────────────────────────────────────────────────────────
    # API pública
    # ────────────────────────────────────────────────────────────────────────

    def find_journey(
        self,
        origin_coords: tuple[float, float],
        destination_coords: tuple[float, float],
        departure_time: datetime,
        num_alternatives: int = 3,
        profile: OptimizationProfile = "balanced",
    ) -> list[Journey]:
        """Encuentra rutas óptimas entre dos coordenadas.

        Args:
            origin_coords: (lat, lon) del origen.
            destination_coords: (lat, lon) del destino.
            departure_time: hora de salida.
            num_alternatives: número de alternativas a devolver.
            profile: perfil de optimización.
                - ``fastest``: prioriza menor tiempo de llegada.
                - ``fewer_transfers``: prioriza menos transbordos.
                - ``less_walking``: prioriza menos distancia caminada.
                - ``balanced``: aplica el costo generalizado (default, OTP-style).

        Returns:
            Lista de Journey ordenados según el perfil.
        """
        self._validate_inputs(origin_coords, destination_coords, departure_time)

        cfg = self.config

        # ── Caminata directa: si destino está al alcance peatonal, sugerirlo
        direct_dist = self._haversine(
            origin_coords[1],
            origin_coords[0],
            destination_coords[1],
            destination_coords[0],
        )
        direct_walks: list[Journey] = []
        if direct_dist <= cfg.max_direct_walk_km:
            direct_walks.append(
                self._build_direct_walk(
                    origin_coords,
                    destination_coords,
                    departure_time,
                    direct_dist,
                )
            )
            # Política Google/Apple Maps: si el origen y destino están a distancia
            # razonablemente caminable, no tiene sentido proponer tránsito.
            if direct_dist <= cfg.max_walking_to_stop_km:
                return direct_walks

        # ── Paradas candidatas en acceso y egreso (budgets de caminata)
        origin_stops = self.gtfs.get_nearby_stops(
            origin_coords,
            margin_km=cfg.max_walking_to_stop_km,
            max_stops=cfg.max_origin_stops,
        )
        destination_stops = self.gtfs.get_nearby_stops(
            destination_coords,
            margin_km=cfg.max_walking_to_stop_km,
            max_stops=cfg.max_destination_stops,
        )

        if not origin_stops or not destination_stops:
            return direct_walks

        # Diccionario destino → distancia de caminata egreso
        dest_walk_dist: dict[str, float] = dict(destination_stops)

        # ── Búsqueda multi-target: una corrida por origen, todos los destinos
        all_journeys: list[Journey] = []
        for origin_stop, origin_dist in origin_stops:
            origin_walk_time = (origin_dist / cfg.walking_speed_kmh) * 3600
            arrival_at_origin_stop = departure_time + timedelta(seconds=origin_walk_time)

            journeys = self._connection_scan_multi_target(
                origin_stop=origin_stop,
                destination_walk_dist=dest_walk_dist,
                start_time=arrival_at_origin_stop,
                origin_coords=origin_coords,
                destination_coords=destination_coords,
                origin_walk_dist=origin_dist,
                actual_departure=departure_time,
            )
            all_journeys.extend(journeys)

        # ── Agregar caminata directa al pool si existía (para que entre al Pareto)
        all_journeys.extend(direct_walks)
        if not all_journeys:
            return []

        # ── Filtrar: budgets totales, duplicados de secuencia de rutas
        all_journeys = [
            j for j in all_journeys if j.total_walking_distance <= cfg.max_total_walking_km
        ]
        if not all_journeys:
            return direct_walks  # fallback razonable

        unique = self._filter_similar_journeys(all_journeys)

        # ── Frente de Pareto 3D (llegada, transbordos, caminata)
        pareto = self._pareto_filter(unique)

        # ── Diversidad cuando el frente es chico
        if len(pareto) < num_alternatives:
            pareto = self._add_diverse_alternatives(pareto, unique, num_alternatives)

        # ── Orden final por perfil
        sorted_journeys = self._sort_by_profile(pareto, profile)
        return sorted_journeys[:num_alternatives]

    # ────────────────────────────────────────────────────────────────────────
    # Núcleo Dijkstra: multi-target
    # ────────────────────────────────────────────────────────────────────────

    def _connection_scan_multi_target(
        self,
        origin_stop: str,
        destination_walk_dist: dict[str, float],
        start_time: datetime,
        origin_coords: tuple[float, float],
        destination_coords: tuple[float, float],
        origin_walk_dist: float,
        actual_departure: datetime,
    ) -> list[Journey]:
        """Una corrida de Dijkstra desde ``origin_stop`` que alcanza todos los
        destinos candidatos en ``destination_walk_dist``.

        Cada vez que un destino se "asienta" (settled) por primera vez, se
        reconstruye su viaje. La búsqueda termina cuando todos los destinos
        están asentados o cuando se rebasa el horizonte temporal.
        """
        cfg = self.config
        dest_set: set[str] = set(destination_walk_dist.keys())
        if not dest_set:
            return []

        earliest_arrival: dict[str, datetime] = {origin_stop: start_time}
        in_connection: dict[str, tuple[str, str, datetime, datetime]] = {}

        # (arrival_time, stop_id, current_route, num_transfers)
        queue: list[tuple[datetime, str, str | None, int]] = [(start_time, origin_stop, None, 0)]

        settled_state: set[tuple[str, str | None]] = set()
        visited_route_at_stop: set[tuple[str, str]] = set()

        reached: dict[str, Journey] = {}
        time_horizon = start_time + timedelta(hours=cfg.time_horizon_hours)
        transfer_step = timedelta(
            seconds=cfg.transfer_buffer_seconds + 60  # buffer + tiempo de embarque típico
        )

        while queue:
            arrival_time, current_stop, current_route, num_transfers = heapq.heappop(queue)

            if arrival_time > time_horizon:
                break  # cola está ordenada por tiempo: más allá nada importa

            if arrival_time > earliest_arrival.get(current_stop, arrival_time):
                continue
            state_key = (current_stop, current_route)
            if state_key in settled_state:
                continue
            settled_state.add(state_key)

            # ── Llegada a un destino candidato
            if current_stop in dest_set and current_stop not in reached:
                journey = self._reconstruct_journey(
                    origin_stop=origin_stop,
                    destination_stop=current_stop,
                    in_connection=in_connection,
                    origin_coords=origin_coords,
                    destination_coords=destination_coords,
                    origin_walk_dist=origin_walk_dist,
                    dest_walk_dist=destination_walk_dist[current_stop],
                    actual_departure=actual_departure,
                )
                if journey is not None:
                    reached[current_stop] = journey
                    if len(reached) == len(dest_set):
                        break  # todos los destinos cubiertos
                # No "continue": una parada destino sirve también como pivote
                # de transbordo para alcanzar otros destinos más adelante.

            if num_transfers > cfg.max_transfers:
                continue

            for route_id in self._get_routes_at_stop(current_stop):
                pair = (current_stop, route_id)
                if pair in visited_route_at_stop:
                    continue
                visited_route_at_stop.add(pair)

                needs_transfer = current_route is not None and current_route != route_id
                if needs_transfer:
                    if not self._is_transfer_viable(current_route, current_stop, route_id):
                        continue
                    dep_time = arrival_time + transfer_step
                else:
                    dep_time = arrival_time

                next_stops = self._get_next_stops_on_route(route_id, current_stop, dep_time)
                if not next_stops:
                    continue

                new_transfers = num_transfers + (1 if needs_transfer else 0)
                for next_stop, next_arrival_time in next_stops:
                    if (
                        next_stop not in earliest_arrival
                        or next_arrival_time < earliest_arrival[next_stop]
                    ):
                        earliest_arrival[next_stop] = next_arrival_time
                        in_connection[next_stop] = (
                            current_stop,
                            route_id,
                            dep_time,
                            next_arrival_time,
                        )
                        heapq.heappush(
                            queue,
                            (next_arrival_time, next_stop, route_id, new_transfers),
                        )

        return list(reached.values())

    # ────────────────────────────────────────────────────────────────────────
    # Helpers de grafo / red GTFS
    # ────────────────────────────────────────────────────────────────────────

    def _get_routes_at_stop(self, stop_id: str) -> list[str]:
        return self._stop_to_routes.get(stop_id, [])

    def _is_transfer_viable(self, from_route: str, stop_id: str, to_route: str) -> bool:
        if not self.transfer_manager:
            return True
        for transfer in self.transfer_manager.get_transfers_from(from_route, stop_id):
            if transfer.to_route_id == to_route and transfer.is_viable():
                # Aplicar también el budget de caminata para transbordo
                if transfer.walking_distance_km <= self.config.max_walking_transfer_km:
                    return True
        return False

    def _get_next_stops_on_route(
        self,
        route_id: str,
        current_stop: str,
        current_time: datetime,
    ) -> list[tuple[str, datetime]]:
        graph = self.gtfs.graphs.get(route_id)
        node_map = self.gtfs._graph_node_maps.get(route_id)
        idx_to_node = self.gtfs._graph_idx_to_node.get(route_id)

        if graph is None or not node_map or current_stop not in node_map:
            return []

        src_idx = node_map[current_stop]
        successor_indices = graph.successor_indices(src_idx)
        if not successor_indices:
            return []

        route_stops = self.gtfs.route_stops.get(route_id, {})
        current_time_only = current_time.time()
        fallback_step = timedelta(minutes=self.config.fallback_step_minutes)
        results: list[tuple[str, datetime]] = []

        for next_idx in successor_indices:
            next_stop_id = idx_to_node.get(next_idx)
            if next_stop_id is None:
                continue

            next_info = route_stops.get(next_stop_id, {})
            arrival_times = next_info.get("arrival_times") or ()

            next_arrival_dt: datetime | None = None
            for t in arrival_times:
                if t >= current_time_only:
                    next_arrival_dt = datetime.combine(current_time.date(), t)
                    break

            if next_arrival_dt and next_arrival_dt > current_time:
                estimated_time = next_arrival_dt
            else:
                estimated_time = current_time + fallback_step

            results.append((next_stop_id, estimated_time))

        return results

    # ────────────────────────────────────────────────────────────────────────
    # Reconstrucción y construcción de Journey
    # ────────────────────────────────────────────────────────────────────────

    def _build_direct_walk(
        self,
        origin_coords: tuple[float, float],
        destination_coords: tuple[float, float],
        departure_time: datetime,
        distance_km: float,
    ) -> Journey:
        walk_time_sec = (distance_km / self.config.walking_speed_kmh) * 3600
        end_time = departure_time + timedelta(seconds=walk_time_sec)
        return Journey(
            segments=[
                {
                    "type": "walk",
                    "from": "origin",
                    "to": "destination",
                    "from_latlon": list(origin_coords),
                    "to_latlon": list(destination_coords),
                    "distance_km": distance_km,
                    "duration": timedelta(seconds=walk_time_sec),
                    "start_time": departure_time,
                    "end_time": end_time,
                }
            ],
            total_duration=timedelta(seconds=walk_time_sec),
            departure_time=departure_time,
            arrival_time=end_time,
            number_of_transfers=0,
            total_walking_distance=distance_km,
        )

    def _reconstruct_journey(
        self,
        origin_stop: str,
        destination_stop: str,
        in_connection: dict[str, tuple[str, str, datetime, datetime]],
        origin_coords: tuple[float, float],
        destination_coords: tuple[float, float],
        origin_walk_dist: float,
        dest_walk_dist: float,
        actual_departure: datetime,
    ) -> Journey | None:
        if destination_stop not in in_connection:
            return None

        # Camino inverso: destination → origin
        path: list[tuple[str, str, str, datetime, datetime]] = []
        current = destination_stop
        while current != origin_stop:
            entry = in_connection.get(current)
            if entry is None:
                return None
            from_stop, route_id, dep_time, arr_time = entry
            path.append((from_stop, current, route_id, dep_time, arr_time))
            current = from_stop
        path.reverse()

        segments: list[dict] = []
        cfg = self.config
        transfer_duration = timedelta(seconds=cfg.transfer_buffer_seconds)

        # Caminata inicial
        walk_time_sec = (origin_walk_dist / cfg.walking_speed_kmh) * 3600
        segments.append(
            {
                "type": "walk",
                "from": "origin",
                "to": origin_stop,
                "from_latlon": [origin_coords[0], origin_coords[1]],
                "distance_km": origin_walk_dist,
                "duration": timedelta(seconds=walk_time_sec),
                "start_time": actual_departure,
                "end_time": actual_departure + timedelta(seconds=walk_time_sec),
            }
        )

        # Tránsito + transbordos
        num_transfers = 0
        prev_route: str | None = None
        for from_stop, to_stop, route_id, dep_time, arr_time in path:
            if prev_route is not None and prev_route != route_id:
                num_transfers += 1
                segments.append(
                    {
                        "type": "transfer",
                        "from_route": prev_route,
                        "to_route": route_id,
                        "at_stop": from_stop,
                        "duration": transfer_duration,
                    }
                )
            segments.append(
                {
                    "type": "transit",
                    "route_id": route_id,
                    "from_stop": from_stop,
                    "to_stop": to_stop,
                    "departure_time": dep_time,
                    "arrival_time": arr_time,
                    "duration": arr_time - dep_time,
                }
            )
            prev_route = route_id

        # Caminata final
        final_walk_time_sec = (dest_walk_dist / cfg.walking_speed_kmh) * 3600
        last_end = (
            segments[-1]["arrival_time"]
            if segments[-1]["type"] == "transit"
            else segments[-1]["end_time"]
        )
        segments.append(
            {
                "type": "walk",
                "from": destination_stop,
                "to": "destination",
                "to_latlon": [destination_coords[0], destination_coords[1]],
                "distance_km": dest_walk_dist,
                "duration": timedelta(seconds=final_walk_time_sec),
                "start_time": last_end,
                "end_time": last_end + timedelta(seconds=final_walk_time_sec),
            }
        )

        total_duration = segments[-1]["end_time"] - segments[0]["start_time"]
        total_walking = origin_walk_dist + dest_walk_dist

        return Journey(
            segments=segments,
            total_duration=total_duration,
            departure_time=actual_departure,
            arrival_time=segments[-1]["end_time"],
            number_of_transfers=num_transfers,
            total_walking_distance=total_walking,
        )

    # ────────────────────────────────────────────────────────────────────────
    # Filtros y selección
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _filter_similar_journeys(journeys: Iterable[Journey]) -> list[Journey]:
        """Filtra viajes con idéntica secuencia de rutas de tránsito."""
        seen: set[tuple[str, ...]] = set()
        unique: list[Journey] = []
        for journey in journeys:
            key = journey.transit_route_signature() or ("__walk__",)
            if key not in seen:
                seen.add(key)
                unique.append(journey)
        return unique

    @staticmethod
    def _pareto_filter(journeys: list[Journey]) -> list[Journey]:
        """Pareto 3D sobre (arrival_time, num_transfers, walking_distance).

        Implementación: ordena por (arrival, transfers, walking); para cada
        candidato, lo agrega si no es dominado por alguno ya admitido. O(n²)
        en peor caso pero n suele ser pequeño (<50 candidatos).
        """
        if not journeys:
            return []

        ordered = sorted(
            journeys,
            key=lambda j: (j.arrival_time, j.number_of_transfers, j.total_walking_distance),
        )

        pareto: list[Journey] = []
        for candidate in ordered:
            c_arr = candidate.arrival_time
            c_xfer = candidate.number_of_transfers
            c_walk = candidate.total_walking_distance
            dominated = False
            for accepted in pareto:
                a_arr = accepted.arrival_time
                a_xfer = accepted.number_of_transfers
                a_walk = accepted.total_walking_distance
                # `accepted` domina a `candidate` si es <= en las tres dims
                # y < en al menos una.
                if (
                    a_arr <= c_arr
                    and a_xfer <= c_xfer
                    and a_walk <= c_walk
                    and (a_arr < c_arr or a_xfer < c_xfer or a_walk < c_walk)
                ):
                    dominated = True
                    break
            if not dominated:
                pareto.append(candidate)

        return pareto

    @staticmethod
    def _add_diverse_alternatives(
        pareto: list[Journey],
        candidates: list[Journey],
        target: int,
    ) -> list[Journey]:
        """Rellena el Pareto con journeys dominados pero con set de rutas distinto."""
        result = list(pareto)
        used: set[frozenset[str]] = {j.transit_route_set() for j in result}
        for journey in candidates:
            if len(result) >= target:
                break
            rset = journey.transit_route_set()
            if rset not in used:
                result.append(journey)
                used.add(rset)
        result.sort()
        return result

    def _sort_by_profile(
        self,
        journeys: list[Journey],
        profile: OptimizationProfile,
    ) -> list[Journey]:
        """Ordena los viajes del Pareto según el perfil pedido."""
        cfg = self.config

        if profile == "fastest":
            return sorted(
                journeys,
                key=lambda j: (j.arrival_time, j.number_of_transfers, j.total_walking_distance),
            )
        if profile == "fewer_transfers":
            return sorted(
                journeys,
                key=lambda j: (j.number_of_transfers, j.arrival_time, j.total_walking_distance),
            )
        if profile == "less_walking":
            return sorted(
                journeys,
                key=lambda j: (j.total_walking_distance, j.arrival_time, j.number_of_transfers),
            )

        # "balanced": costo generalizado OTP-style
        #   score = duración + transbordos × penalty_costo + caminata × penalty_caminata
        # El walking_cost equivale al tiempo extra que un usuario aceptaría caminar
        # para evitar 1 km extra (~5 min adicionales por km, OTP-style).
        walking_cost_seconds_per_km = 5 * 60
        xfer_cost = cfg.transfer_cost_penalty_seconds

        def cost(j: Journey) -> float:
            return (
                j.total_duration.total_seconds()
                + j.number_of_transfers * xfer_cost
                + j.total_walking_distance * walking_cost_seconds_per_km
            )

        return sorted(journeys, key=cost)

    # ────────────────────────────────────────────────────────────────────────
    # Utilidades
    # ────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _validate_inputs(
        origin_coords: tuple[float, float],
        destination_coords: tuple[float, float],
        departure_time: datetime,
    ) -> None:
        if not isinstance(origin_coords, tuple) or len(origin_coords) != 2:
            raise ValueError("origin_coords debe ser una tupla (lat, lon)")
        if not isinstance(destination_coords, tuple) or len(destination_coords) != 2:
            raise ValueError("destination_coords debe ser una tupla (lat, lon)")
        if not isinstance(departure_time, datetime):
            raise ValueError("departure_time debe ser un objeto datetime")

    @staticmethod
    def _haversine(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
        """Distancia haversine en km."""
        rl1, rl2 = radians(lat1), radians(lat2)
        dl = radians(lat2 - lat1) * 0.5
        dl2 = radians(lon2 - lon1) * 0.5
        a = sin(dl) ** 2 + cos(rl1) * cos(rl2) * sin(dl2) ** 2
        return _EARTH_RADIUS_KM * 2.0 * asin(sqrt(a))


def create_csa_planner(
    gtfs_data,
    transfer_manager=None,
    config: CSAConfig | None = None,
    # — compat hacia atrás —
    max_walking_km: float | None = None,
    walking_speed_kmh: float | None = None,
    max_transfers: int | None = None,
) -> ConnectionScanAlgorithm:
    """Factory de ConnectionScanAlgorithm.

    Acepta un ``CSAConfig`` o, alternativamente, los kwargs legacy.
    """
    return ConnectionScanAlgorithm(
        gtfs_data,
        transfer_manager,
        config=config,
        max_walking_distance_km=max_walking_km,
        walking_speed_kmh=walking_speed_kmh,
        max_transfers=max_transfers,
    )
