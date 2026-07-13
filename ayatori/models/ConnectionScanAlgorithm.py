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
from bisect import bisect_left
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from math import asin, cos, radians, sin, sqrt
from operator import itemgetter
from typing import Literal

_EARTH_RADIUS_KM = 6371.0
_SECS_DAY = 24 * 3600
_FIRST = itemgetter(0)  # clave de orden/bisect sobre boardings (segundos-del-día)

OptimizationProfile = Literal[
    "fastest", "fewer_transfers", "less_walking", "balanced", "prefer_rail"
]

# Modos GTFS canónicos reconocidos (ver GTFSData._ROUTE_TYPE_TO_MODE).
KNOWN_MODES = (
    "bus",
    "metro",
    "rail",
    "tram",
    "ferry",
    "cable",
    "gondola",
    "funicular",
)

# Sesgo por defecto de "prefer_rail": penaliza (en costo, no en tiempo) abordar
# bus y favorece metro/tren. Solo afecta el ranking final, no la búsqueda.
_PREFER_RAIL_TRANSFER_PENALTY = {
    "bus": 240,
    "metro": -180,
    "rail": -240,
    "tram": -60,
}


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

    # ── Modo de transporte (consciente de modo) ──────────────────────────────
    # Todos default None/() → sin sesgo, conducta idéntica a la histórica.
    allowed_modes: tuple[str, ...] | None = None  # si se setea, solo estos abordables
    excluded_modes: tuple[str, ...] = ()  # modos nunca abordables
    # Sesgo (segundos) sumado al costo generalizado por cada abordaje de ese
    # modo. Negativo = preferir. NO afecta el eje de tiempo de Dijkstra.
    mode_transfer_penalty_seconds: dict | None = None
    # Multiplicador del tiempo en vehículo por modo (solo costo "balanced").
    mode_preference_weight: dict | None = None
    # Budget de caminata de acceso/egreso por modo (km). Solo se usa cuando hay
    # filtro de modo activo: amplía la búsqueda de paradas candidatas para
    # alcanzar estaciones de metro/tren (más dispersas que las de bus). El tope
    # global ``max_total_walking_km`` sigue aplicando. None = sin override.
    mode_access_walk_km: dict | None = None

    # Ruteo peatonal real (OSM). Solo efectivo si se pasa un OSMGraph al
    # constructor; si no, o si shortest_path falla, cae a Haversine.
    use_osm_walking: bool = True

    # Calendario: respetar calendar.txt/calendar_dates.txt según la fecha de
    # salida. Si no hay calendario o queda vacío, se cae al pool de horarios
    # sin filtrar (comportamiento histórico) — nunca devuelve "0 viajes" por
    # esto. Poner en False fuerza el comportamiento antiguo (debug/benchmark).
    respect_calendar: bool = True


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
        osm_graph=None,
    ):
        self.gtfs = gtfs_data
        self.transfer_manager = transfer_manager
        self.osm = osm_graph
        self.config = config or CSAConfig()

        # Cache route_id → modo (resuelto vía GTFSData.get_route_mode)
        self._route_mode_cache: dict = {}

        # Cache (from_route, stop_id, to_route) → viabilidad del transbordo.
        # Independiente del tiempo/origen → se reusa entre corridas y llamadas.
        self._transfer_viable_cache: dict[tuple[str, str, str], bool] = {}

        # Índice stop→rutas (GTFSData siempre lo construye en __init__)
        self._stop_to_routes: dict = gtfs_data._stop_to_routes

        # Si hay filtros de modo activos, pre-filtrar stop→rutas una sola vez
        # para evitar reevaluar _mode_allowed en el hot path de Dijkstra.
        # Mutar config.allowed_modes/excluded_modes tras construir el planner
        # no surte efecto: reconstruir el planner si cambian.
        if self.config.allowed_modes is not None or self.config.excluded_modes:
            self._stop_to_routes_filtered: dict = {
                sid: [r for r in routes if self._mode_allowed(r)]
                for sid, routes in self._stop_to_routes.items()
            }
        else:
            self._stop_to_routes_filtered = self._stop_to_routes

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

        # ── Servicios activos en la fecha de salida (calendar.txt + excepciones)
        active_services: frozenset = frozenset()
        if cfg.respect_calendar and hasattr(self.gtfs, "active_services_on"):
            active_services = self.gtfs.active_services_on(departure_time.date())
        # Cache de boardings por (route_id, stop_id, active_services). Persiste en
        # el GTFSData (vive todo el proceso) en vez de por-request: la expansión de
        # frecuencias es determinística para una fecha dada, así que la API —que
        # reconstruye un CSA por request— no la re-paga en cada consulta.
        # ponytail: crece con el working set de paradas consultadas; materializar
        # TODO costaría ~600 MB (ver _build_route_index). Cap LRU si la huella molesta.
        active_times_cache = getattr(self.gtfs, "_boardings_cache", None)
        if active_times_cache is None:
            active_times_cache = {}
            try:
                self.gtfs._boardings_cache = active_times_cache
            except AttributeError:
                pass  # gtfs stub sin __dict__ → cae a cache por-request

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
        origin_stops = self._candidate_stops(
            origin_coords, cfg.max_walking_to_stop_km, cfg.max_origin_stops
        )
        destination_stops = self._candidate_stops(
            destination_coords, cfg.max_walking_to_stop_km, cfg.max_destination_stops
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
                active_services=active_services,
                active_times_cache=active_times_cache,
            )
            all_journeys.extend(journeys)

        # ── Agregar caminata directa al pool si existía (para que entre al Pareto)
        all_journeys.extend(direct_walks)
        if not all_journeys:
            return []

        # ── Filtrar: budgets totales, tope de transbordos, duplicados
        # El grafo Dijkstra optimiza llegada (in_connection indexado por parada),
        # así que el camino mínimo en tiempo puede encadenar más cambios de ruta
        # que max_transfers. Se descartan aquí para nunca exceder lo pedido.
        all_journeys = [
            j
            for j in all_journeys
            if j.total_walking_distance <= cfg.max_total_walking_km
            and j.number_of_transfers <= cfg.max_transfers
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
        result = sorted_journeys[:num_alternatives]

        # ── Ruteo peatonal OSM solo sobre lo que se devuelve. Pareto/orden se
        # deciden con Haversine (comportamiento pre-OSM); pagar shortest_path
        # por cada destino alcanzado durante el scan era el mayor costo del
        # request con OSM activo.
        self._apply_osm_walks(result)
        # _apply_osm_walks reajusta llegada/caminata de los N devueltos: una
        # caminata que en línea recta era corta puede alargarse al seguir calles.
        # Se reordena con las métricas ya OSM para que el orden mostrado sea
        # coherente con lo que ve el usuario (p. ej. "fastest" encabeza con la
        # menor hora de llegada real, no la Haversine). Solo reordena los N ya
        # elegidos; no cambia cuáles se devuelven.
        return self._sort_by_profile(result, profile)

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
        active_services: frozenset = frozenset(),
        active_times_cache: dict | None = None,
    ) -> list[Journey]:
        """Una corrida de Dijkstra desde ``origin_stop`` que alcanza todos los
        destinos candidatos en ``destination_walk_dist``.

        El estado del scan trackea el ``trip_id`` activo (no sólo la ruta), por
        lo que:
        - Para abordar una ruta en una parada se exige que exista un trip cuyo
          horario en esa parada sea ≥ a la hora actual. Sin esto, el algoritmo
          asumía que se podía tomar cualquier bus cuyo horario en la PRÓXIMA
          parada fuera futuro — incluso si ya había pasado por la actual.
        - Las continuaciones (siguiente parada dentro del mismo bus) siguen el
          sequence real del trip, no la "primera llegada disponible" en la
          parada siguiente. Esto evita Frankensteins de varios trips.
        - ``dep_time`` y ``arr_time`` guardados en ``in_connection`` son los
          horarios reales del trip — la duración del segmento ya no incluye el
          tiempo de espera del bus en el paradero.
        """
        cfg = self.config
        dest_set: set[str] = set(destination_walk_dist.keys())
        if not dest_set:
            return []
        if active_times_cache is None:
            active_times_cache = {}

        earliest_arrival: dict[str, datetime] = {origin_stop: start_time}
        in_connection: dict[str, tuple[str, str, datetime, datetime]] = {}
        # Metadata de transbordos caminando (footpaths): clave = to_stop,
        # valor = TransferConnection usado para reconstruir el segmento a pie.
        walk_meta: dict[str, object] = {}

        # (arrival_time, counter, stop_id, route_id|None, trip_id|None, num_transfers)
        # counter rompe empates determinísticamente y evita que heapq compare
        # None < str cuando dos llegadas coinciden al microsegundo.
        counter = 0
        queue: list[tuple] = [(start_time, counter, origin_stop, None, None, 0)]

        settled_state: set[tuple[str, str | None]] = set()
        visited_route_at_stop: set[tuple[str, str]] = set()

        reached: dict[str, Journey] = {}
        time_horizon = start_time + timedelta(hours=cfg.time_horizon_hours)
        transfer_step = timedelta(
            seconds=cfg.transfer_buffer_seconds + 60  # buffer + tiempo de embarque típico
        )

        while queue:
            (
                arrival_time,
                _ctr,
                current_stop,
                current_route,
                current_trip,
                num_transfers,
            ) = heapq.heappop(queue)

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
                    walk_meta=walk_meta,
                )
                if journey is not None:
                    reached[current_stop] = journey
                    if len(reached) == len(dest_set):
                        break  # todos los destinos cubiertos
                # No "continue": una parada destino sirve también como pivote
                # de transbordo para alcanzar otros destinos más adelante.

            if num_transfers > cfg.max_transfers:
                continue

            # ── Continuación: si vengo arriba de un trip, seguir al próximo stop
            # del MISMO bus. No es transbordo y no requiere abordaje.
            if current_trip is not None:
                adv = self._next_stop_on_trip(current_trip, current_stop, arrival_time)
                if adv is not None:
                    next_stop, next_arr_dt = adv
                    if (
                        next_arr_dt >= arrival_time
                        and next_arr_dt <= time_horizon
                        and (
                            next_stop not in earliest_arrival
                            or next_arr_dt < earliest_arrival[next_stop]
                        )
                    ):
                        earliest_arrival[next_stop] = next_arr_dt
                        in_connection[next_stop] = (
                            current_stop,
                            current_route,
                            arrival_time,  # dep_time = hora real del trip en current_stop
                            next_arr_dt,
                        )
                        counter += 1
                        heapq.heappush(
                            queue,
                            (
                                next_arr_dt,
                                counter,
                                next_stop,
                                current_route,
                                current_trip,
                                num_transfers,
                            ),
                        )

            # ── Abordaje de otra ruta (con o sin transbordo según si vengo en bus)
            for route_id in self._get_routes_at_stop(current_stop):
                # Si ya estoy en esta ruta, la continuación de arriba se encarga.
                if current_trip is not None and route_id == current_route:
                    continue

                pair = (current_stop, route_id)
                if pair in visited_route_at_stop:
                    continue
                visited_route_at_stop.add(pair)

                # _mode_allowed ya aplicado a nivel de _stop_to_routes_filtered.

                # "needs_transfer" = vengo arriba de un bus → cambiar a otro es transbordo.
                # Si current_trip es None vengo a pie (origen o footpath) → no es transbordo.
                needs_transfer = current_trip is not None
                if needs_transfer:
                    if num_transfers + 1 > cfg.max_transfers:
                        continue
                    if not self._is_transfer_viable(current_route, current_stop, route_id):
                        continue
                    board_after = arrival_time + transfer_step
                else:
                    board_after = arrival_time

                boarding = self._next_boarding(
                    route_id,
                    current_stop,
                    board_after,
                    active_services,
                    active_times_cache,
                )
                if boarding is None:
                    continue
                board_dt, trip_id = boarding

                adv = self._next_stop_on_trip(trip_id, current_stop, board_dt)
                if adv is None:
                    continue  # current_stop es la última del trip — no se puede avanzar
                next_stop, next_arr_dt = adv
                if next_arr_dt > time_horizon:
                    continue

                new_transfers = num_transfers + (1 if needs_transfer else 0)
                if (
                    next_stop not in earliest_arrival
                    or next_arr_dt < earliest_arrival[next_stop]
                ):
                    earliest_arrival[next_stop] = next_arr_dt
                    in_connection[next_stop] = (
                        current_stop,
                        route_id,
                        board_dt,  # dep_time = hora real de boarding (no tu llegada al paradero)
                        next_arr_dt,
                    )
                    counter += 1
                    heapq.heappush(
                        queue,
                        (
                            next_arr_dt,
                            counter,
                            next_stop,
                            route_id,
                            trip_id,
                            new_transfers,
                        ),
                    )

            # ── Footpath: caminar a una parada cercana distinta. ES el transbordo,
            # por eso se empuja con current_trip=None (la próxima subida no recuenta).
            if (
                current_trip is not None
                and self.transfer_manager is not None
                and num_transfers + 1 <= cfg.max_transfers
            ):
                for tr in self.transfer_manager.get_transfers_from(
                    current_route, current_stop
                ):
                    to_stop = tr.to_stop_id
                    if (
                        to_stop == current_stop
                        or not tr.is_viable()
                        or tr.walking_distance_km > cfg.max_walking_transfer_km
                    ):
                        continue
                    walk_secs = tr.walking_time_seconds + cfg.transfer_buffer_seconds
                    walk_arr = arrival_time + timedelta(seconds=walk_secs)
                    if walk_arr > time_horizon:
                        continue
                    if (
                        to_stop not in earliest_arrival
                        or walk_arr < earliest_arrival[to_stop]
                    ):
                        earliest_arrival[to_stop] = walk_arr
                        in_connection[to_stop] = (
                            current_stop,
                            "__walk__",
                            arrival_time,
                            walk_arr,
                        )
                        walk_meta[to_stop] = tr
                        counter += 1
                        heapq.heappush(
                            queue,
                            (walk_arr, counter, to_stop, None, None, num_transfers + 1),
                        )

        return list(reached.values())

    # ────────────────────────────────────────────────────────────────────────
    # Helpers de grafo / red GTFS
    # ────────────────────────────────────────────────────────────────────────

    def _get_routes_at_stop(self, stop_id: str) -> list[str]:
        # Devuelve la lista pre-filtrada por modo (idéntica al raw si no hay filtros).
        return self._stop_to_routes_filtered.get(stop_id, [])

    def _mode_of(self, route_id: str) -> str:
        """Modo de una ruta (cacheado). ``bus`` si GTFSData no lo expone."""
        mode = self._route_mode_cache.get(route_id)
        if mode is None:
            getter = getattr(self.gtfs, "get_route_mode", None)
            mode = getter(route_id) if getter else "bus"
            self._route_mode_cache[route_id] = mode
        return mode

    def _candidate_stops(self, coords, margin_km: float, max_stops: int):
        """Paradas de acceso/egreso respetando filtros de modo.

        Sin filtros activos → comportamiento histórico (N más cercanas).
        Con filtros → se consulta un pool más amplio y se conservan solo las
        paradas que sirven un modo permitido, evitando que la densidad de
        paradas de bus desplace a las estaciones de metro/tren del top-N.
        """
        cfg = self.config
        filters_active = cfg.allowed_modes is not None or bool(cfg.excluded_modes)
        if not filters_active:
            return self.gtfs.get_nearby_stops(
                coords, margin_km=margin_km, max_stops=max_stops
            )

        # Budget de acceso ampliado por modo (opt-in): permite alcanzar
        # estaciones de metro/tren más lejanas que la parada de bus más cercana.
        eff_margin = margin_km
        if cfg.mode_access_walk_km:
            relevant = (
                cfg.allowed_modes
                if cfg.allowed_modes is not None
                else [m for m in KNOWN_MODES if m not in cfg.excluded_modes]
            )
            for m in relevant:
                v = cfg.mode_access_walk_km.get(m)
                if v is not None and v > eff_margin:
                    eff_margin = v

        # Filter-then-rank: traer TODAS las paradas dentro del radio (cota alta,
        # inalcanzable dentro de un margen peatonal) y filtrar DESPUÉS por modo.
        # Truncar por proximidad antes de filtrar dejaba fuera estaciones de
        # metro/tren útiles en zonas densas de paraderos de bus.
        pool = self.gtfs.get_nearby_stops(
            coords, margin_km=eff_margin, max_stops=1_000_000
        )
        # Candidatura por PERTENENCIA DE MODO (no abordabilidad): la plataforma
        # terminal de una línea de metro es la última parada de todo trip, así que
        # no tiene ruta abordable en _stop_to_routes, pero es un egreso válido.
        # gtfs.stop_modes incluye terminales; _get_routes_at_stop (abordar) no.
        filtered = [(sid, d) for sid, d in pool if self._stop_mode_allowed(sid)]
        # Si el filtro deja todo vacío, devolver el pool acotado (find_journey
        # ya maneja el caso de no encontrar viajes y cae a caminata directa).
        if not filtered:
            return pool[:max_stops]
        return filtered[:max_stops]

    def _mode_allowed(self, route_id: str) -> bool:
        """Aplica los filtros allowed_modes / excluded_modes del config."""
        cfg = self.config
        if not cfg.excluded_modes and cfg.allowed_modes is None:
            return True
        mode = self._mode_of(route_id)
        if cfg.excluded_modes and mode in cfg.excluded_modes:
            return False
        if cfg.allowed_modes is not None and mode not in cfg.allowed_modes:
            return False
        return True

    def _stop_mode_allowed(self, stop_id: str) -> bool:
        """¿La parada es servida por algún modo permitido? (incluye terminales).

        Análogo a ``_mode_allowed`` pero a nivel de PARADA, usando el índice
        ``gtfs.stop_modes`` (que incluye la última parada de cada trip). Se usa
        para seleccionar paradas de acceso/egreso bajo filtro de modo, sin exigir
        que la ruta sea *abordable* allí (el terminal de una línea no lo es, pero
        sirve como egreso). Si el índice no existe (gtfs stub), cae al criterio de
        abordabilidad para no romper.
        """
        cfg = self.config
        modes = getattr(self.gtfs, "stop_modes", None)
        if modes is None:
            return bool(self._get_routes_at_stop(stop_id))
        stop_ms = modes.get(stop_id)
        if not stop_ms:
            return False
        for m in stop_ms:
            if cfg.excluded_modes and m in cfg.excluded_modes:
                continue
            if cfg.allowed_modes is not None and m not in cfg.allowed_modes:
                continue
            return True
        return False

    def _is_transfer_viable(self, from_route: str, stop_id: str, to_route: str) -> bool:
        if not self.transfer_manager:
            return True
        key = (from_route, stop_id, to_route)
        cached = self._transfer_viable_cache.get(key)
        if cached is not None:
            return cached
        max_walk = self.config.max_walking_transfer_km
        result = False
        for transfer in self.transfer_manager.get_transfers_from(from_route, stop_id):
            if (
                transfer.to_route_id == to_route
                and transfer.is_viable()
                and transfer.walking_distance_km <= max_walk
            ):
                result = True
                break
        self._transfer_viable_cache[key] = result
        return result

    def _active_boardings(
        self,
        route_id: str,
        stop_id: str,
        stop_info: dict,
        active_services: frozenset,
        cache: dict,
    ) -> list[tuple]:
        """Lista ordenada de ``(time, trip_id, dispatch_secs)`` de abordajes válidos.

        Construye los boardings expandiendo dispatches (frequencies.txt) sobre
        los trips que visitan esta parada. La expansión se hace **bajo demanda**
        y se cachea por request (``cache`` es ``active_times_cache`` del scan):
        materializar al arranque cuesta ~600 MB en Santiago.

        Si hay ``active_services`` se filtra por ``service_id`` del trip; si la
        intersección queda vacía se cae al pool sin filtrar (mismo patrón que
        el viejo ``_active_arrival_times`` — robusto ante gaps de calendar.txt).
        """
        key = (route_id, stop_id, active_services)
        cached = cache.get(key)
        if cached is not None:
            return cached

        trips_here = stop_info.get("trips_here") or []
        if not trips_here:
            cache[key] = []
            return []

        trip_service = self.gtfs.trip_service
        trip_dispatches = self.gtfs.trip_dispatches

        def _expand(filter_service: bool) -> list:
            # Boardings como (tod_secs, trip_id, dispatch_secs), tod en segundos
            # del día (int). Antes se construía un datetime.time por boarding (12M+
            # objetos por consulta) y se ordenaba con un lambda; el int es un orden
            # estrictamente monótono equivalente, así que el resultado del bisect
            # en _next_boarding es idéntico — solo mucho más barato.
            out: list = []
            for trip_id, offset_secs in trips_here:
                if filter_service:
                    sid = trip_service.get(trip_id)
                    if sid is None or sid not in active_services:
                        continue
                dispatches = trip_dispatches.get(trip_id)
                if not dispatches:
                    continue
                for d in dispatches:
                    out.append(((d + offset_secs) % _SECS_DAY, trip_id, d))
            out.sort(key=_FIRST)
            return out

        boardings: list = []
        if active_services:
            boardings = _expand(filter_service=True)
        if not boardings:  # sin filtro o filtro vacío → pool completo
            boardings = _expand(filter_service=False)

        cache[key] = boardings
        return boardings

    def _next_boarding(
        self,
        route_id: str,
        stop_id: str,
        after_time: datetime,
        active_services: frozenset = frozenset(),
        active_times_cache: dict | None = None,
    ) -> tuple[datetime, tuple[str, int]] | None:
        """Primer trip de ``route_id`` cuyo horario en ``stop_id`` ≥ ``after_time``.

        El "trip virtual" es ``(trip_id, dispatch_secs)``: en GTFS frequency-based
        el mismo trip se despacha varias veces durante el día y cada dispatch es
        un bus distinto. Sin esto el motor cree que un bus pasa una sola vez al
        día — origen del bug "viajes imposiblemente rápidos".

        Devuelve ``(boarding_datetime, (trip_id, dispatch_secs))`` o ``None``.
        """
        route_stops = self.gtfs.route_stops.get(route_id)
        if not route_stops:
            return None
        stop_info = route_stops.get(stop_id)
        if not stop_info:
            return None

        if active_times_cache is None:
            active_times_cache = {}
        boardings = self._active_boardings(
            route_id, stop_id, stop_info, active_services, active_times_cache
        )
        if not boardings:
            return None

        # Segundos del día de after_time, con la fracción incluida: el bisect
        # contra tods enteros reproduce exactamente la frontera del antiguo
        # comparador de datetime.time (que sí distingue microsegundos, presentes
        # en after_time cuando viene de un tiempo de caminata fraccionario).
        after_tod = (
            after_time.hour * 3600
            + after_time.minute * 60
            + after_time.second
            + after_time.microsecond / 1_000_000
        )
        idx = bisect_left(boardings, after_tod, key=_FIRST)
        if idx >= len(boardings):
            return None

        tod, trip_id, dispatch_secs = boardings[idx]
        board_time_only = time(tod // 3600, (tod // 60) % 60, tod % 60)
        board_dt = datetime.combine(after_time.date(), board_time_only)
        if board_dt < after_time:
            return None
        return board_dt, (trip_id, dispatch_secs)

    def _next_stop_on_trip(
        self,
        virtual_trip: tuple[str, int],
        current_stop: str,
        ref_dt: datetime,
    ) -> tuple[str, datetime] | None:
        """Siguiente ``(stop_id, arrival_datetime)`` del trip virtual.

        ``virtual_trip = (trip_id, dispatch_secs)``: el ``trip_id`` indexa la
        secuencia de offsets (segundos desde el inicio del trip); el
        ``dispatch_secs`` indica cuándo salió este bus en particular (expandido
        desde frequencies.txt o la hora absoluta de la primera parada).

        ``ref_dt`` ancla la fecha; si el tiempo absoluto resultante cae antes de
        ``ref_dt`` se asume cruce de medianoche y se suma un día.
        """
        trip_id, dispatch_secs = virtual_trip
        trip_seq = self.gtfs.trips.get(trip_id)
        if not trip_seq:
            return None
        idx_map = self.gtfs.trip_stop_idx.get(trip_id)
        if not idx_map:
            return None
        idx = idx_map.get(current_stop)
        if idx is None or idx + 1 >= len(trip_seq):
            return None
        next_stop, next_offset = trip_seq[idx + 1]
        next_abs_secs = dispatch_secs + next_offset
        next_arr_dt = datetime.combine(ref_dt.date(), datetime.min.time()) + timedelta(
            seconds=next_abs_secs
        )
        if next_arr_dt < ref_dt:
            next_arr_dt += timedelta(days=1)
        return next_stop, next_arr_dt

    # ────────────────────────────────────────────────────────────────────────
    # Reconstrucción y construcción de Journey
    # ────────────────────────────────────────────────────────────────────────

    def _walk(self, from_latlon, to_latlon, straight_km: float):
        """Distancia/geometría peatonal real (OSM) con fallback a Haversine.

        Args:
            from_latlon: ``(lat, lon)`` origen del tramo a pie.
            to_latlon: ``(lat, lon)`` destino del tramo a pie.
            straight_km: distancia Haversine ya calculada (fallback).

        Returns:
            ``(distancia_km, polyline | None)``. Si OSM no está disponible o la
            ruta es absurda (snapping a isla desconectada), devuelve
            ``(straight_km, None)`` — comportamiento histórico.
        """
        if (
            self.osm is None
            or not self.config.use_osm_walking
            or from_latlon is None
            or to_latlon is None
        ):
            return straight_km, None
        try:
            res = self.osm.shortest_path(from_latlon, to_latlon)
        except Exception:
            return straight_km, None
        if not res:
            return straight_km, None
        dist_km, poly = res
        # Guarda de cordura: una ruta OSM > 3x la línea recta suele ser
        # snapping a un nodo desconectado → preferir Haversine.
        if straight_km > 0 and dist_km > straight_km * 3:
            return straight_km, None
        return dist_km, poly

    def _apply_osm_walks(self, journeys: list[Journey]) -> None:
        """Enriquece con ruteo OSM los tramos a pie de acceso/egreso, in place.

        Se llama únicamente sobre los ≤ ``num_alternatives`` viajes que
        ``find_journey`` retorna. Ajusta distancia, duración, geometría y los
        agregados del Journey (llegada, duración total, caminata total).
        Los footpaths de transbordo ya traen distancia/polyline OSM desde el
        TransferManager, y la caminata directa se construye con OSM — ambos
        se reconocen por tener ``path`` o faltarles un extremo, y se saltan.
        """
        if self.osm is None or not self.config.use_osm_walking:
            return
        speed = self.config.walking_speed_kmh
        for j in journeys:
            changed = False
            for seg in j.segments:
                if seg.get("type") != "walk" or "path" in seg:
                    continue
                from_ll = seg.get("from_latlon")
                to_ll = seg.get("to_latlon")
                if from_ll is None or to_ll is None:
                    continue
                dist_km, poly = self._walk(from_ll, to_ll, seg["distance_km"])
                if poly:
                    seg["path"] = poly
                if dist_km != seg["distance_km"]:
                    j.total_walking_distance += dist_km - seg["distance_km"]
                    seg["distance_km"] = dist_km
                    walk_secs = (dist_km / speed) * 3600
                    seg["duration"] = timedelta(seconds=walk_secs)
                    seg["end_time"] = seg["start_time"] + seg["duration"]
                    changed = True
            if changed:
                last_end = j.segments[-1].get("end_time")
                if last_end is not None:
                    j.arrival_time = last_end
                    j.total_duration = last_end - j.departure_time

    def _build_direct_walk(
        self,
        origin_coords: tuple[float, float],
        destination_coords: tuple[float, float],
        departure_time: datetime,
        distance_km: float,
    ) -> Journey:
        osm_km, poly = self._walk(origin_coords, destination_coords, distance_km)
        distance_km = osm_km
        walk_time_sec = (distance_km / self.config.walking_speed_kmh) * 3600
        end_time = departure_time + timedelta(seconds=walk_time_sec)
        walk_seg = {
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
        if poly:
            walk_seg["path"] = poly
        return Journey(
            segments=[walk_seg],
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
        walk_meta: dict | None = None,
    ) -> Journey | None:
        if destination_stop not in in_connection:
            return None
        walk_meta = walk_meta or {}

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

        # ── Colapsar footpath colgante final ────────────────────────────────
        # Si el destino se alcanzó CAMINANDO desde otra parada (hop "__walk__"
        # final) y no se aborda nada después, ese footpath es SIEMPRE dominado:
        # por desigualdad triangular, caminar directo desde donde se bajó del
        # vehículo (S→destino) es ≤ el desvío S→paradero→destino, y ahorra un
        # transbordo espurio. Se colapsa dentro de la caminata de egreso: la
        # parada de bajada real pasa a ser el punto de egreso. Además recupera
        # viajes de 0 transbordos cuando la estación útil (p. ej. un terminal de
        # metro) quedó fuera del top-N de max_destination_stops y sólo se podía
        # "llegar" caminando a un paradero contiguo. (Un footpath sólo se emite
        # tras bajar de un vehículo, así que a lo sumo hay uno colgante.)
        if path and path[-1][2] == "__walk__":
            alight_stop = path[-1][0]
            alight_coords = self.gtfs.get_stop_coords(alight_stop)
            if alight_coords is not None:
                path.pop()
                destination_stop = alight_stop
                dest_walk_dist = self._haversine(
                    alight_coords[0],
                    alight_coords[1],
                    destination_coords[1],
                    destination_coords[0],
                )

        segments: list[dict] = []
        cfg = self.config
        transfer_duration = timedelta(seconds=cfg.transfer_buffer_seconds)

        # Caminata inicial (origen → primera parada). Distancia Haversine: el
        # ruteo OSM se aplica después y SOLO a los viajes que se devuelven
        # (_apply_osm_walks) — hacerlo aquí costaba 2 Dijkstras OSM por cada
        # destino alcanzado en el scan (hasta ~128 por request).
        o_stop_c = self.gtfs.get_stop_coords(origin_stop)
        o_stop_latlon = (
            [o_stop_c[1], o_stop_c[0]] if o_stop_c is not None else None
        )
        walk_time_sec = (origin_walk_dist / cfg.walking_speed_kmh) * 3600
        first_walk = {
            "type": "walk",
            "from": "origin",
            "to": origin_stop,
            "from_latlon": [origin_coords[0], origin_coords[1]],
            "distance_km": origin_walk_dist,
            "duration": timedelta(seconds=walk_time_sec),
            "start_time": actual_departure,
            "end_time": actual_departure + timedelta(seconds=walk_time_sec),
        }
        if o_stop_latlon is not None:
            first_walk["to_latlon"] = o_stop_latlon
        segments.append(first_walk)

        # Tránsito + transbordos (incluye footpaths: hops con route "__walk__")
        num_transfers = 0
        transfer_walk_km = 0.0
        prev_route: str | None = None
        pending_walk_seg: dict | None = None  # footpath a completar con to_route
        for from_stop, to_stop, route_id, dep_time, arr_time in path:
            if route_id == "__walk__":
                # Transbordo caminando entre paradas distintas (footpath).
                tr = walk_meta.get(to_stop)
                num_transfers += 1
                dist_km = getattr(tr, "walking_distance_km", 0.0) if tr else 0.0
                transfer_walk_km += dist_km
                seg = {
                    "type": "transfer",
                    "from_route": prev_route,
                    "to_route": None,
                    "at_stop": from_stop,
                    "from_stop": from_stop,
                    "to_stop": to_stop,
                    "distance_km": dist_km,
                    "duration": arr_time - dep_time,
                    "start_time": dep_time,
                    "end_time": arr_time,
                    "from_mode": self._mode_of(prev_route) if prev_route else None,
                    "to_mode": None,
                }
                fp = getattr(tr, "walking_path", None) if tr else None
                if fp:
                    seg["path"] = fp
                segments.append(seg)
                pending_walk_seg = seg
                # El footpath ES el transbordo; la próxima subida no recuenta.
                prev_route = None
                continue

            if prev_route is not None and prev_route != route_id:
                num_transfers += 1
                segments.append(
                    {
                        "type": "transfer",
                        "from_route": prev_route,
                        "to_route": route_id,
                        "at_stop": from_stop,
                        "duration": transfer_duration,
                        "from_mode": self._mode_of(prev_route),
                        "to_mode": self._mode_of(route_id),
                    }
                )
            if pending_walk_seg is not None:
                # Completar el footpath con la ruta/modo que se aborda al llegar.
                pending_walk_seg["to_route"] = route_id
                pending_walk_seg["to_mode"] = self._mode_of(route_id)
                pending_walk_seg = None
            transit_seg = {
                "type": "transit",
                "route_id": route_id,
                "from_stop": from_stop,
                "to_stop": to_stop,
                "departure_time": dep_time,
                "arrival_time": arr_time,
                "duration": arr_time - dep_time,
                "mode": self._mode_of(route_id),
            }
            meta = None
            getter = getattr(self.gtfs, "get_route_meta", None)
            if getter:
                meta = getter(route_id)
            if meta:
                transit_seg["route_short_name"] = meta.get("short_name")
                transit_seg["route_long_name"] = meta.get("long_name")
                transit_seg["agency_name"] = meta.get("agency_name")
                transit_seg["route_color"] = meta.get("route_color")
            segments.append(transit_seg)
            prev_route = route_id

        # Caminata final (última parada → destino). Haversine; OSM diferido a
        # _apply_osm_walks (misma razón que la caminata inicial).
        d_stop_c = self.gtfs.get_stop_coords(destination_stop)
        d_stop_latlon = (
            [d_stop_c[1], d_stop_c[0]] if d_stop_c is not None else None
        )
        final_walk_time_sec = (dest_walk_dist / cfg.walking_speed_kmh) * 3600
        _last = segments[-1]
        last_end = _last.get("arrival_time") or _last.get("end_time")
        last_walk = {
            "type": "walk",
            "from": destination_stop,
            "to": "destination",
            "to_latlon": [destination_coords[0], destination_coords[1]],
            "distance_km": dest_walk_dist,
            "duration": timedelta(seconds=final_walk_time_sec),
            "start_time": last_end,
            "end_time": last_end + timedelta(seconds=final_walk_time_sec),
        }
        if d_stop_latlon is not None:
            last_walk["from_latlon"] = d_stop_latlon
        segments.append(last_walk)

        total_duration = segments[-1]["end_time"] - segments[0]["start_time"]
        total_walking = origin_walk_dist + dest_walk_dist + transfer_walk_km

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

        # "balanced" / "prefer_rail": costo generalizado OTP-style
        #   score = duración + transbordos × penalty_costo + caminata × penalty_caminata
        #           + sesgo de modo (peso de duración + penalty por abordaje)
        # El walking_cost equivale al tiempo extra que un usuario aceptaría caminar
        # para evitar 1 km extra (~5 min adicionales por km, OTP-style).
        #
        # El sesgo de modo se aplica SOLO aquí (ranking post-Pareto), nunca en el
        # eje de tiempo de Dijkstra: un delta negativo en el tiempo volvería la
        # búsqueda inadmisible. Esto es cómo OTP expone "preferencias de modo".
        walking_cost_seconds_per_km = 5 * 60
        xfer_cost = cfg.transfer_cost_penalty_seconds

        mode_penalty = cfg.mode_transfer_penalty_seconds
        mode_weight = cfg.mode_preference_weight
        if profile == "prefer_rail" and mode_penalty is None:
            mode_penalty = _PREFER_RAIL_TRANSFER_PENALTY

        def mode_delta(j: Journey) -> float:
            if not mode_penalty and not mode_weight:
                return 0.0
            delta = 0.0
            for s in j.segments:
                if s.get("type") != "transit":
                    continue
                mode = s.get("mode", "bus")
                if mode_penalty:
                    delta += mode_penalty.get(mode, 0)
                if mode_weight:
                    secs = s.get("duration")
                    if secs is not None:
                        delta += secs.total_seconds() * (
                            mode_weight.get(mode, 1.0) - 1.0
                        )
            return delta

        def cost(j: Journey) -> float:
            return (
                j.total_duration.total_seconds()
                + j.number_of_transfers * xfer_cost
                + j.total_walking_distance * walking_cost_seconds_per_km
                + mode_delta(j)
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
