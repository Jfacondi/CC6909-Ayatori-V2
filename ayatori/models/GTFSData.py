import csv
import io
import json
import logging
import os
import warnings
import zipfile
from collections import defaultdict
from itertools import pairwise
from math import asin, cos, radians, sin, sqrt

import numpy as np
import pygtfs
from scipy.spatial import cKDTree

from ..utils.gtfs_cleaner import clean_gtfs_stops

_EARTH_RADIUS_KM = 6371.0


class GTFSData:
    """Lectura de un feed GTFS y construcción de índices para ruteo.

    Carga un GTFS (.zip o directorio extraído) en memoria vía pygtfs y construye
    estructuras necesarias para el motor CSA: un cKDTree espacial sobre las
    paradas, un grafo dirigido por ruta (rustworkx), y dos índices invertidos
    (parada→rutas, parada→coords) que permiten consultas O(1).

    Attributes:
        scheduler: ``pygtfs.Schedule`` con el feed cargado.
        route_stops: ``{route_id: {stop_id: {sequence, coordinates, trips_here}}}``.
        stops: conjunto de stop_ids vistos en alguna ruta.
        transfer_manager: presente después de ``get_or_compute_transfers``.

    Example:
        >>> gtfs = GTFSData("santiago-gtfs.zip")
        >>> tm = gtfs.get_or_compute_transfers(cache_path="cache.json")
        >>> nearby = gtfs.get_nearby_stops((-33.43, -70.65), margin_km=0.5)
    """

    def __init__(self, GTFS_PATH="gtfs.zip"):
        import time as _t
        _phase_t = _t.perf_counter()
        def _tick(label):
            nonlocal _phase_t
            now = _t.perf_counter()
            print(f"[GTFSData] {label}: {now - _phase_t:.1f}s", flush=True)
            _phase_t = now

        self.scheduler = self.create_scheduler(GTFS_PATH)
        _tick("create_scheduler")
        self.route_stops = {}
        self.stops = set()
        self.route_meta: dict = {}  # {route_id: {route_type, agency, names, mode}}
        self.stop_names: dict = {}  # {stop_id: stop_name}
        # Índices por trip: el CSA los usa para evitar "abordar un bus que ya pasó"
        # y para expandir frecuencias correctamente (GTFS frequency-based).
        # trips[trip_id]      = [(stop_id, offset_secs), ...] ordenado por stop_sequence;
        #                       offset_secs = segundos desde la PRIMERA parada del trip.
        # trip_stop_idx[t][s] = posición de la parada s en la secuencia del trip t.
        # trip_service[t]     = service_id (para filtrar por calendario).
        # trip_route[t]       = route_id (back-reference).
        # trip_dispatches[t]  = lista ordenada de instantes (segundos desde 00:00) en
        #                       los que el trip se "despacha" — un dispatch por cada
        #                       expansión de frequencies.txt. Para trips sin frecuencia,
        #                       contiene un único dispatch = hora absoluta de la primera
        #                       parada (recupera el comportamiento estándar).
        # Tiempo absoluto del trip en su parada k = dispatch_secs + offset_secs[k].
        self.trips: dict = {}
        self.trip_stop_idx: dict = {}
        self.trip_service: dict = {}
        self.trip_route: dict = {}
        self.trip_dispatches: dict = {}
        self.get_gtfs_data()  # puebla route_stops y los índices por trip
        _tick("get_gtfs_data")
        self._expand_frequencies()
        _tick("expand_frequencies")
        self.stops = self.get_stop_ids()
        _tick("get_stop_ids")
        self._build_spatial_index()
        _tick("build_spatial_index")
        self._build_route_index()
        _tick("build_route_index")
        self._build_shape_index()
        _tick("build_shape_index")
        self._build_route_meta()
        _tick("build_route_meta")
        self._build_calendar_index()
        _tick("build_calendar_index")

    def _build_spatial_index(self):
        """Construye un índice espacial para búsquedas rápidas de paradas.

        Filtra ``location_type`` para incluir sólo paradas físicas (lt=0).
        El feed 2026 declara entradas (lt=2), pathway nodes (lt=3) y boarding
        areas (lt=4) como rows separadas en stops.txt — útiles para describir
        la anatomía interna de estaciones de Metro pero no son lugares donde
        un usuario sube/baja del vehículo. Si entran al cKDTree, el snap
        espacial puede devolver "top de escalera AG:ESCN01_TOP" en vez de la
        parada con servicios, y el motor descarta el candidato silenciosamente.

        stop_name se indexa siempre (sin filtro) para que los segmentos puedan
        resolver nombres de paradas referenciadas por id.
        """
        self._stop_ids_list = []
        coords = []
        for stop in self.scheduler.stops:
            if stop.stop_name is not None:
                self.stop_names[stop.stop_id] = stop.stop_name
            # location_type vacío/ausente == "0" según la spec GTFS.
            lt = getattr(stop, "location_type", None)
            if lt is not None and lt != "" and int(lt) != 0:
                continue
            if stop.stop_lat is not None and stop.stop_lon is not None:
                try:
                    lat = float(stop.stop_lat)
                    lon = float(stop.stop_lon)
                    self._stop_ids_list.append(stop.stop_id)
                    coords.append([lat, lon])
                except (ValueError, TypeError):
                    continue

        if coords:
            self._stop_coords_array = np.array(coords)
            self._spatial_tree = cKDTree(self._stop_coords_array)
        else:
            self._spatial_tree = None

    def _expand_frequencies(self):
        """Expande ``frequencies.txt`` en una lista de dispatches por trip.

        En GTFS frequency-based, ``stop_times`` contiene tiempos relativos (el
        primer stop suele ser 00:00:00) y ``frequencies.txt`` indica cada cuánto
        sale ese trip en cada ventana de horario. Sin expandir esto el motor
        cree que un bus pasa una vez al día a las 00:00 — origen del bug
        "viajes imposiblemente rápidos" (el fallback de 2 min/hop se activaba
        para todas las consultas diurnas).

        Para cada record ``(trip_id, start_time, end_time, headway_secs)`` se
        generan dispatches en ``[start, end)`` cada ``headway_secs``. Trips sin
        record en frequencies.txt conservan su dispatch único (asignado en
        ``get_gtfs_data`` como la hora absoluta de su primera parada).
        """
        freqs = getattr(self.scheduler, "frequencies", None) or []
        if not freqs:
            return

        expanded: dict = {}
        for f in freqs:
            tid = getattr(f, "trip_id", None)
            if tid is None or tid not in self.trips:
                continue
            try:
                start = int(f.start_time.total_seconds())
                end = int(f.end_time.total_seconds())
                headway = int(f.headway_secs)
            except (AttributeError, TypeError, ValueError):
                continue
            if headway <= 0 or end <= start:
                continue

            expanded.setdefault(tid, []).extend(range(start, end, headway))

        # Sobreescribir solo los trips con frequencies (preserva el dispatch único
        # de los demás). Ordenar para garantizar invariante.
        for tid, disp_list in expanded.items():
            disp_list.sort()
            self.trip_dispatches[tid] = disp_list

    def _build_route_index(self):
        """
        Construye índices para búsquedas rápidas:
          - _stop_to_routes: {stop_id: [route_id, ...]}  → O(1) por parada
          - _sorted_route_stops: {route_id: [(stop_id, stop_info), ...]} ordenado por secuencia
          - _stop_coords: {stop_id: (lon, lat)}  → O(1) lookup de coordenadas
          - trips_here por (route, stop): índice ligero {trip_id, offset_at_stop}
            que el CSA usa para construir boardings absolutos *bajo demanda*
            (expandiendo dispatches). Materializar todos los boardings al
            arranque cuesta ~600 MB en Santiago — esto baja la huella a ~20 MB
            sin perder corrección (el CSA cachea por request).
        """
        self._stop_to_routes: dict = defaultdict(list)
        self._sorted_route_stops: dict = {}
        self._stop_coords: dict = {}

        for route_id, stops_dict in self.route_stops.items():
            for stop_id, stop_info in stops_dict.items():
                self._stop_to_routes[stop_id].append(route_id)
                if stop_id not in self._stop_coords:
                    coords = stop_info.get("coordinates")
                    if coords:
                        self._stop_coords[stop_id] = coords
                stop_info["trips_here"] = []

            self._sorted_route_stops[route_id] = sorted(
                stops_dict.items(),
                key=lambda kv: kv[1]["sequence"],
            )

        # Indexar qué trips pasan por cada (route, stop) y a qué offset (segundos
        # desde la primera parada del trip). El CSA usa esto + trip_dispatches +
        # trip_service para reconstruir boardings absolutos cuando los necesita.
        # Sólo paradas que NO son la última del trip (la última no permite avanzar).
        for trip_id, trip_seq in self.trips.items():
            route_id = self.trip_route.get(trip_id)
            if route_id is None:
                continue
            stops_dict = self.route_stops.get(route_id)
            if not stops_dict:
                continue
            for stop_id, offset_secs in trip_seq[:-1]:
                stop_info = stops_dict.get(stop_id)
                if stop_info is None:
                    continue
                stop_info["trips_here"].append((trip_id, offset_secs))

        # Completar coords de paradas presentes en el feed pero ausentes de
        # route_stops (entradas de Metro, pathway nodes, paraderos sin servicio).
        # Esto se hace UNA vez al arranque, en el hilo que creó el scheduler
        # SQLite — así get_stop_coords nunca consulta la DB en tiempo de request
        # (la sesión SQLite de pygtfs no es válida fuera de su hilo de origen, y
        # los endpoints síncronos de FastAPI corren en otro hilo del threadpool).
        for stop in self.scheduler.stops:
            stop_id = stop.stop_id
            if stop_id in self._stop_coords:
                continue
            if stop.stop_lon is None or stop.stop_lat is None:
                continue
            try:
                self._stop_coords[stop_id] = (float(stop.stop_lon), float(stop.stop_lat))
            except (ValueError, TypeError):
                continue

    def _build_shape_index(self):
        """Indexa shapes.txt para trazar polylines reales de cada ruta.

        Resultado:
          - ``_shapes_by_id[shape_id]``: ``np.ndarray (N, 2)`` con ``[lat, lon]``
            ordenado por ``shape_pt_sequence``.
          - ``_route_dir_main_shape[(route_id, direction_id)]``: shape_id más común
            entre los trips de esa (ruta, dirección).
          - ``_route_dir_stop_order[(route_id, direction_id)]``: ``{stop_id: pos}``
            con la posición del stop dentro del trip más completo en esa dirección.
          - ``_route_dir_stops_ordered[(route_id, direction_id)]``: ``[stop_id, ...]``
            paradas ordenadas por secuencia (mismo trip representativo). Permite
            fallback "polyline por paradas" cuando no hay shape.

        ``direction_id`` se normaliza a 0/1; trips sin dirección caen en 0.
        """
        self._shapes_by_id: dict = {}
        self._route_dir_main_shape: dict = {}
        self._route_dir_stop_order: dict = {}
        self._route_dir_stops_ordered: dict = {}

        sched = self.scheduler

        raw_shapes: dict = defaultdict(list)
        for sp in getattr(sched, "shapes", []) or []:
            try:
                lat = float(sp.shape_pt_lat)
                lon = float(sp.shape_pt_lon)
                seq = int(sp.shape_pt_sequence)
            except (TypeError, ValueError):
                continue
            raw_shapes[sp.shape_id].append((seq, lat, lon))

        for shape_id, pts in raw_shapes.items():
            pts.sort(key=lambda t: t[0])
            self._shapes_by_id[shape_id] = np.array([[lat, lon] for _, lat, lon in pts])

        shape_counts: dict = defaultdict(lambda: defaultdict(int))
        trip_stop_counts: dict = defaultdict(lambda: (None, -1))
        # Cuello de botella detectado en el feed 2026 (26k trips): acceder a
        # ``trip.stop_times`` y luego ``st.stop_sequence`` para cada
        # ``StopTime`` dispara queries SQL en cadena vía SQLAlchemy lazy-load.
        # Los datos ya están en memoria en ``self.trips`` (poblado por
        # ``get_gtfs_data``, ordenados por stop_sequence), así que los
        # reutilizamos en vez de re-leer del scheduler.
        for trip in sched.trips:
            shape_id = getattr(trip, "shape_id", None)
            route_id = trip.route_id
            try:
                direction = int(getattr(trip, "direction_id", 0) or 0)
            except (TypeError, ValueError):
                direction = 0
            key = (route_id, direction)

            if shape_id and shape_id in self._shapes_by_id:
                shape_counts[key][shape_id] += 1

            trip_seq = self.trips.get(trip.trip_id)
            if trip_seq:
                n = len(trip_seq)
                _, best_n = trip_stop_counts[key]
                if n > best_n:
                    trip_stop_counts[key] = (trip.trip_id, n)
                    self._route_dir_stop_order[key] = {
                        sid: i for i, (sid, _) in enumerate(trip_seq)
                    }
                    self._route_dir_stops_ordered[key] = [sid for sid, _ in trip_seq]

        for key, counts in shape_counts.items():
            self._route_dir_main_shape[key] = max(counts.items(), key=lambda kv: kv[1])[0]

    # GTFS route_type → modo canónico (https://gtfs.org/schedule/reference/#routestxt)
    _ROUTE_TYPE_TO_MODE = {
        0: "tram",
        1: "metro",
        2: "rail",
        3: "bus",
        4: "ferry",
        5: "cable",
        6: "gondola",
        7: "funicular",
    }

    @classmethod
    def _mode_from_route_type(cls, route_type) -> str:
        """Mapea un ``route_type`` GTFS a un modo canónico. Default ``bus``."""
        try:
            rt = int(route_type)
        except (TypeError, ValueError):
            return "bus"
        return cls._ROUTE_TYPE_TO_MODE.get(rt, "bus")

    def _build_route_meta(self):
        """Indexa metadata por ruta: route_type, agencia, nombres y modo.

        Aditivo: el ruteo no cambia, solo se expone metadata para el motor
        consciente de modo y la UI. O(#rutas), una vez al arranque.
        """
        self.route_meta = {}
        for r in self.scheduler.routes:
            agency = getattr(r, "agency", None)
            rt = r.route_type
            self.route_meta[r.route_id] = {
                "route_id": r.route_id,
                "route_type": int(rt) if rt is not None else 3,
                "agency_id": r.agency_id,
                "agency_name": getattr(agency, "agency_name", None),
                "short_name": r.route_short_name,
                "long_name": r.route_long_name,
                "route_color": r.route_color,
                "mode": self._mode_from_route_type(rt if rt is not None else 3),
            }

    def get_route_meta(self, route_id: str) -> dict | None:
        """Metadata de una ruta (route_type, agencia, nombres, modo) o None."""
        return self.route_meta.get(route_id)

    def get_route_mode(self, route_id: str) -> str:
        """Modo canónico de una ruta (``bus`` por defecto)."""
        meta = self.route_meta.get(route_id)
        return meta["mode"] if meta else "bus"

    def available_modes(self) -> list[str]:
        """Modos canónicos presentes en el feed, en orden de ``route_type``.

        Útil para la UI: sólo tiene sentido ofrecer filtros para modos que
        existen realmente en el GTFS cargado.
        """
        present = {meta["mode"] for meta in self.route_meta.values()}
        return [m for m in self._ROUTE_TYPE_TO_MODE.values() if m in present]

    def get_stop_name(self, stop_id: str) -> str | None:
        """Nombre legible de una parada, o None si no se conoce."""
        return self.stop_names.get(stop_id)

    def _build_calendar_index(self):
        """Indexa calendar.txt + calendar_dates.txt para filtrar por fecha.

        - ``self.services[service_id] = {"weekdays": (lun..dom bool), "start", "end"}``
        - ``self.service_exceptions[service_id] = [(date, exception_type), ...]``
          donde exception_type 1=agrega servicio, 2=lo quita ese día.
        """
        self.services: dict = {}
        self.service_exceptions: dict = defaultdict(list)
        self._active_svc_cache: dict = {}

        for svc in getattr(self.scheduler, "services", []) or []:
            try:
                weekdays = (
                    bool(svc.monday),
                    bool(svc.tuesday),
                    bool(svc.wednesday),
                    bool(svc.thursday),
                    bool(svc.friday),
                    bool(svc.saturday),
                    bool(svc.sunday),
                )
            except AttributeError:
                continue
            self.services[svc.service_id] = {
                "weekdays": weekdays,
                "start": getattr(svc, "start_date", None),
                "end": getattr(svc, "end_date", None),
            }

        for ex in getattr(self.scheduler, "service_exceptions", []) or []:
            try:
                etype = int(ex.exception_type)
            except (TypeError, ValueError):
                continue
            self.service_exceptions[ex.service_id].append((ex.date, etype))

    def active_services_on(self, query_date) -> frozenset:
        """Conjunto de ``service_id`` activos en ``query_date`` (datetime.date).

        Aplica el rango ``[start, end]`` y el día de semana de calendar.txt, y
        luego las excepciones de calendar_dates.txt (1=agrega, 2=quita).
        Devuelve ``frozenset`` vacío si no hay calendario (el motor cae al
        comportamiento sin filtro como fallback).
        """
        if not getattr(self, "services", None) and not getattr(
            self, "service_exceptions", None
        ):
            return frozenset()

        cached = self._active_svc_cache.get(query_date)
        if cached is not None:
            return cached

        wd = query_date.weekday()  # 0=lunes
        active = set()
        for sid, c in self.services.items():
            start, end = c["start"], c["end"]
            if start is not None and query_date < start:
                continue
            if end is not None and query_date > end:
                continue
            if c["weekdays"][wd]:
                active.add(sid)

        for sid, exs in self.service_exceptions.items():
            for d, etype in exs:
                if d == query_date:
                    if etype == 1:
                        active.add(sid)
                    elif etype == 2:
                        active.discard(sid)

        result = frozenset(active)
        self._active_svc_cache[query_date] = result
        return result

    def feed_date_range(self) -> tuple[str | None, str | None]:
        """Rango ``[start, end]`` de fechas con servicio en el feed (ISO).

        Combina los rangos de ``calendar.txt`` con las fechas de
        ``calendar_dates.txt``. Devuelve ``(None, None)`` si no hay calendario.
        """
        starts, ends = [], []
        for c in getattr(self, "services", {}).values():
            if c.get("start") is not None:
                starts.append(c["start"])
            if c.get("end") is not None:
                ends.append(c["end"])
        for exs in getattr(self, "service_exceptions", {}).values():
            for d, _etype in exs:
                starts.append(d)
                ends.append(d)
        if not starts or not ends:
            return None, None
        return min(starts).isoformat(), max(ends).isoformat()

    def get_route_shape_segment(
        self, route_id: str, from_stop: str, to_stop: str
    ) -> list[list[float]] | None:
        """Polyline real (lista de ``[lat, lon]``) entre dos paradas de una ruta.

        Elige la ``direction_id`` donde ``from_stop`` precede a ``to_stop``,
        toma el shape representativo de esa (ruta, dirección) y lo recorta
        proyectando cada parada al punto más cercano del polyline.

        Returns:
            Lista de ``[lat, lon]`` con al menos 2 puntos, o ``None`` si no hay
            shape utilizable (cae al render lineal del frontend).
        """
        if not hasattr(self, "_shapes_by_id") or not self._shapes_by_id:
            return None

        candidate_dirs = []
        for direction in (0, 1):
            order = self._route_dir_stop_order.get((route_id, direction))
            if not order:
                continue
            i_from = order.get(from_stop)
            i_to = order.get(to_stop)
            if i_from is None or i_to is None:
                continue
            if i_from < i_to:
                candidate_dirs.append((direction, i_to - i_from))
        if not candidate_dirs:
            return None
        direction = min(candidate_dirs, key=lambda t: t[1])[0]

        shape_id = self._route_dir_main_shape.get((route_id, direction))
        if shape_id is None:
            return None
        shape = self._shapes_by_id.get(shape_id)
        if shape is None or len(shape) < 2:
            return None

        from_coords = self.get_stop_coords(from_stop)
        to_coords = self.get_stop_coords(to_stop)
        if from_coords is None or to_coords is None:
            return None

        from_lat, from_lon = from_coords[1], from_coords[0]
        to_lat, to_lon = to_coords[1], to_coords[0]

        d_from = (shape[:, 0] - from_lat) ** 2 + (shape[:, 1] - from_lon) ** 2
        d_to = (shape[:, 0] - to_lat) ** 2 + (shape[:, 1] - to_lon) ** 2
        idx_from = int(np.argmin(d_from))
        idx_to = int(np.argmin(d_to))
        if idx_from > idx_to:
            idx_from, idx_to = idx_to, idx_from
        if idx_to - idx_from < 1:
            return None

        seg = shape[idx_from : idx_to + 1]
        # Guard contra avenidas de doble sentido: el shape pasa dos veces cerca de
        # una parada y argmin puede latchear en la pasada equivocada, haciendo que
        # el recorte abarque casi toda la ruta (la "forma rara" en el mapa: un tramo
        # de 240 m dibujado como 30 km). Si el arco resulta absurdamente más largo
        # que la distancia recta entre paradas, descartar el shape y caer al render
        # lineal del tramo (get_route_stops_segment / línea recta).
        # ponytail: heurístico simple; el fix exhaustivo sería proyección monotónica
        # de todas las paradas sobre el shape (más código + cache por ruta/dirección).
        diffs = np.diff(seg, axis=0)
        arc_km = float(
            np.sqrt(diffs[:, 0] ** 2 + (diffs[:, 1] * np.cos(np.radians(from_lat))) ** 2).sum()
            * 111.0
        )
        straight_km = self.haversine(from_lon, from_lat, to_lon, to_lat)
        if arc_km > max(0.4, 4.0 * straight_km):
            return None

        return seg.tolist()

    def get_route_stops_segment(
        self, route_id: str, from_stop: str, to_stop: str
    ) -> list[list[float]] | None:
        """Polyline aproximada usando las paradas intermedias de la ruta.

        Fallback de :meth:`get_route_shape_segment` cuando no hay shape disponible
        (sirve para evitar tramos en línea recta cuando un bus recorre calles).
        Toma la (ruta, dirección) donde ``from_stop`` precede a ``to_stop`` en el
        trip representativo y devuelve los ``[lat, lon]`` de cada parada entre
        ambos extremos (inclusive).

        Returns:
            Lista de ``[lat, lon]`` con al menos 2 puntos, o ``None`` si no se
            pudo determinar el orden.
        """
        if not getattr(self, "_route_dir_stops_ordered", None):
            return None

        best: tuple[list, int, int] | None = None  # (stops_ordered, i_from, i_to)
        for direction in (0, 1):
            stops_ordered = self._route_dir_stops_ordered.get((route_id, direction))
            order = self._route_dir_stop_order.get((route_id, direction))
            if not stops_ordered or not order:
                continue
            i_from = order.get(from_stop)
            i_to = order.get(to_stop)
            if i_from is None or i_to is None or i_from >= i_to:
                continue
            length = i_to - i_from
            if best is None or length < (best[2] - best[1]):
                best = (stops_ordered, i_from, i_to)
        if best is None:
            return None
        stops_ordered, i_from, i_to = best

        out: list[list[float]] = []
        for sid in stops_ordered[i_from : i_to + 1]:
            c = self.get_stop_coords(sid)
            if c is not None:
                out.append([c[1], c[0]])
        return out if len(out) >= 2 else None

    # Modos cuyas shapes vale la pena sintetizar sobre la red vial (OSM).
    # Metro/rail no siguen calles (túneles, vías propias); ferry/aerial menos.
    _SYNTH_SHAPE_MODES = ("bus", "tram")
    _SYNTH_SHAPE_PREFIX = "__synth__:"

    def compute_synthetic_shapes(
        self,
        osm_graph,
        *,
        modes: tuple = _SYNTH_SHAPE_MODES,
        max_pair_km: float = 5.0,
        progress_every: int = 50,
    ) -> dict:
        """Construye shapes sintéticas (polylines OSM) para rutas sin shape GTFS.

        Encadena ``OSMGraph.shortest_path`` entre paradas consecutivas y registra
        el resultado en :attr:`_shapes_by_id` y :attr:`_route_dir_main_shape`
        bajo un shape_id sintético ``__synth__:{route_id}:{direction}``, de modo
        que :meth:`get_route_shape_segment` funciona sin cambios.

        Args:
            osm_graph: instancia de :class:`OSMGraph` ya cargada. Si es
                ``None``, no se hace nada.
            modes: modos canónicos cuyas rutas siguen la red vial. Metro/rail/
                ferry/aerial se saltan (su línea recta es razonable).
            max_pair_km: si dos paradas consecutivas están más lejos que esto,
                ese tramo cae a recta (evita patologías sin abortar el shape
                completo).
            progress_every: log cada N rutas procesadas.

        Returns:
            ``{(route_id, direction): np.ndarray}`` con shapes sintéticas
            nuevas (no incluye rutas con shape real preexistente).
        """
        if osm_graph is None or not getattr(self, "_route_dir_stops_ordered", None):
            return {}

        modes_set = set(modes)
        candidates = [
            (rid, d)
            for (rid, d) in self._route_dir_stops_ordered
            if (rid, d) not in self._route_dir_main_shape
            and self.get_route_mode(rid) in modes_set
        ]
        n_total = len(candidates)
        if n_total == 0:
            print("Sin rutas candidatas para shapes sintéticas (todas tienen shape o no aplican).")
            return {}

        print(f"Computando shapes sintéticas por OSM para {n_total} (ruta, dirección)...")
        synthetic: dict = {}
        empty_routes = 0
        for i, (route_id, direction) in enumerate(candidates):
            stops_ordered = self._route_dir_stops_ordered[(route_id, direction)]
            coords: list[tuple[float, float]] = []
            for sid in stops_ordered:
                c = self.get_stop_coords(sid)
                if c is not None:
                    coords.append((c[1], c[0]))  # (lat, lon)
            if len(coords) < 2:
                empty_routes += 1
                continue

            polyline: list[list[float]] = []
            for a, b in pairwise(coords):
                seg = None
                dist_km = self.haversine(a[1], a[0], b[1], b[0])
                if dist_km <= max_pair_km:
                    try:
                        res = osm_graph.shortest_path(a, b)
                    except Exception:
                        res = None
                    if res:
                        osm_km, osm_poly = res
                        # Rutas OSM excesivamente largas respecto al cruce
                        # directo indican un error topológico → caer a recta.
                        if dist_km == 0 or osm_km <= dist_km * 3:
                            seg = osm_poly
                if not seg or len(seg) < 2:
                    seg = [[a[0], a[1]], [b[0], b[1]]]

                # Evita duplicar el punto de empalme (último de seg anterior ==
                # primero del siguiente, si la red devolvió el mismo nodo).
                if polyline:
                    polyline.extend(seg[1:])
                else:
                    polyline.extend(seg)

            if len(polyline) < 2:
                empty_routes += 1
                continue

            arr = np.asarray(polyline, dtype=float)
            shape_id = f"{self._SYNTH_SHAPE_PREFIX}{route_id}:{direction}"
            self._shapes_by_id[shape_id] = arr
            self._route_dir_main_shape[(route_id, direction)] = shape_id
            synthetic[(route_id, direction)] = arr

            if (i + 1) % progress_every == 0:
                print(f"  {i + 1}/{n_total} rutas procesadas")

        print(
            f"Shapes sintéticas listas: {len(synthetic)} ok / "
            f"{empty_routes} saltadas / {n_total} candidatas."
        )
        return synthetic

    def get_or_compute_synthetic_shapes(
        self,
        cache_path: str | None,
        osm_graph,
    ) -> dict:
        """Carga shapes sintéticas desde cache si existe; si no, las computa
        y las guarda.

        Esquema del JSON::

            [{"route_id": "...", "direction": 0, "shape": [[lat, lon], ...]}, ...]

        Args:
            cache_path: archivo JSON (``None`` = no persistir).
            osm_graph: requerido para computar; opcional para sólo cargar cache.

        Returns:
            Dict de shapes sintéticas registradas en esta corrida
            ``{(route_id, direction): np.ndarray}``.
        """
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    records = json.load(f)
                loaded: dict = {}
                for rec in records:
                    rid = rec["route_id"]
                    d = int(rec["direction"])
                    poly = rec.get("shape")
                    if not poly or len(poly) < 2:
                        continue
                    # Si entretanto el feed adquirió un shape real para esta
                    # (ruta, dirección), el sintético queda obsoleto: ni lo
                    # cargamos para evitar dejar huérfanos en _shapes_by_id.
                    if (rid, d) in self._route_dir_main_shape:
                        continue
                    arr = np.asarray(poly, dtype=float)
                    shape_id = f"{self._SYNTH_SHAPE_PREFIX}{rid}:{d}"
                    self._shapes_by_id[shape_id] = arr
                    self._route_dir_main_shape[(rid, d)] = shape_id
                    loaded[(rid, d)] = arr
                print(f"Shapes sintéticas cargadas desde cache: {len(loaded)} registros.")
                return loaded
            except Exception as e:
                print(f"Cache de shapes sintéticas inválido ({e}), recomputando...")

        if osm_graph is None:
            return {}

        synthetic = self.compute_synthetic_shapes(osm_graph)
        if cache_path and synthetic:
            try:
                records = [
                    {"route_id": rid, "direction": int(d), "shape": arr.tolist()}
                    for (rid, d), arr in synthetic.items()
                ]
                cache_dir = os.path.dirname(cache_path)
                if cache_dir:
                    os.makedirs(cache_dir, exist_ok=True)
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(records, f, ensure_ascii=False)
                print(f"Cache de shapes sintéticas guardado en: {cache_path}")
            except Exception as e:
                print(f"No se pudo guardar cache de shapes sintéticas: {e}")

        return synthetic

    @staticmethod
    def _feed_has_invalid_stops(gtfs_path) -> bool:
        """Detecta si stops.txt tiene rows sin coordenadas válidas.

        pygtfs registra `ERROR pygtfs.loader | Failure while writing Stops(...)`
        para cada row con ``stop_lat=None``/``stop_lon=None`` y los descarta
        silenciosamente. El feed 2026 trae 5990 pathway nodes (lt=2/3/4) sin
        coords, llenando los logs y dejando el comportamiento subóptimo —
        mejor pre-limpiar el feed y pasarle a pygtfs sólo paradas válidas.
        """
        from pathlib import Path
        path = Path(gtfs_path)
        if not path.exists():
            return False
        try:
            with zipfile.ZipFile(path) as zf:
                if "stops.txt" not in zf.namelist():
                    return False
                with zf.open("stops.txt") as f:
                    reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
                    for row in reader:
                        if not (row.get("stop_lat", "").strip() and row.get("stop_lon", "").strip()):
                            return True
        except Exception:
            return False
        return False

    def create_scheduler(self, GTFS_PATH):
        """
        Creates the scheduler for the class, using the GTFS file, located in the given path directory.

        Parameters:
        GTFS_PATH (PATH): the path where the GTFS file is located.

        Returns:
        pygtfs.Schedule: the scheduler object
        """
        # Suprimir advertencias de pygtfs sobre paradas inválidas
        logging.getLogger("pygtfs").setLevel(logging.ERROR)
        warnings.filterwarnings("ignore")

        # Cache SQLite persistente junto al feed: parsear CSV→SQLite cuesta
        # minutos y es determinístico para un zip dado. Si el sidecar existe y
        # es más nuevo que el feed, se abre directo sin re-parsear. Un feed
        # nuevo (mtime mayor) invalida el cache automáticamente.
        db_path = str(GTFS_PATH) + ".sqlite"
        try:
            if os.path.exists(db_path) and os.path.getmtime(db_path) >= os.path.getmtime(
                str(GTFS_PATH)
            ):
                scheduler = pygtfs.Schedule(db_path)
                if scheduler.routes:  # DB vacía/corrupta → reconstruir abajo
                    print(f"GTFS cargado desde cache SQLite: {db_path}")
                    return scheduler
        except Exception as e:
            print(f"Cache SQLite inválido ({e}), re-parseando feed...")

        # Pre-limpieza proactiva: el feed 2026 trae stops sin coords (pathway
        # nodes, entradas) que pygtfs descarta loggeando ERROR pero sin lanzar
        # excepción. Detectamos eso peek-eando stops.txt y limpiamos antes de
        # cargar, para no ensuciar el log y garantizar el mismo resultado.
        gtfs_to_use = GTFS_PATH
        if self._feed_has_invalid_stops(GTFS_PATH):
            try:
                gtfs_to_use = clean_gtfs_stops(GTFS_PATH)
                print(f"GTFS pre-limpiado (paradas sin coords descartadas): {gtfs_to_use}")
            except Exception as e:
                print(f"Pre-limpieza falló ({e}), intentando con feed original")
                gtfs_to_use = GTFS_PATH

        # Intentar cargar GTFS (ya limpiado si correspondía)
        try:
            return self._parse_feed(gtfs_to_use, db_path)
        except (TypeError, ValueError) as e:
            # Fallback: si aún falla por coordenadas None, reintentar con cleaner
            # (cubre el caso en que la pre-limpieza no se ejecutó por algún motivo).
            if "float()" in str(e) and "NoneType" in str(e):
                gtfs_to_use = clean_gtfs_stops(GTFS_PATH)
                return self._parse_feed(gtfs_to_use, db_path)
            raise

    @staticmethod
    def _parse_feed(feed_path, db_path):
        """Parsea el feed en un SQLite persistente; si el disco falla, en memoria."""
        try:
            if os.path.exists(db_path):
                os.remove(db_path)
            scheduler = pygtfs.Schedule(db_path)
        except Exception:
            # Directorio de solo lectura, lock, etc. → sin cache, igual que antes.
            scheduler = pygtfs.Schedule(":memory:")
            db_path = None
        try:
            pygtfs.append_feed(scheduler, str(feed_path))
        except Exception:
            # No dejar un .sqlite a medio escribir que un arranque futuro
            # confunda con un cache válido. En Windows hay que cerrar la
            # conexión antes de poder borrar el archivo.
            if db_path:
                try:
                    scheduler.session.close()
                    scheduler.engine.dispose()
                except Exception:
                    pass
                try:
                    os.remove(db_path)
                except OSError:
                    pass
            raise
        return scheduler

    def get_gtfs_data(self):
        """
        Reads the GTFS data from a file and creates a directed graph with its info, using the 'pygtfs' library. This gives
        the transit feed data of Santiago's public transport, including "Red Metropolitana de Movilidad" (previously known
        as Transantiago), "Metro de Santiago", "EFE Trenes de Chile", and "Buses de Acercamiento Aeropuerto".

        Puebla ``self.route_stops`` y los índices por trip (``trips``,
        ``trip_stop_idx``, ``trip_service``, ``trip_route``, ``trip_dispatches``).
        """
        sched = self.scheduler

        # Una sola pasada por trips y stops. Iterar ``sched.trips`` re-consulta
        # SQLite y materializa ~todos los objetos ORM cada vez; hacerlo dentro
        # del loop de rutas era O(rutas × trips) (~10M materializaciones).
        # ``stops_by_id(...)`` era además una query SQL por parada.
        trips_by_route: dict = defaultdict(list)
        for trip in sched.trips:
            trips_by_route[trip.route_id].append(trip)
        stops_by_id = {stop.stop_id: stop for stop in sched.stops}

        stop_coords = {}

        for route in sched.routes:
            trips = trips_by_route.get(route.route_id, [])

            for trip in trips:
                stop_times = trip.stop_times
                service_id = getattr(trip, "service_id", None)

                # Índice por trip: el CSA necesita la secuencia completa del trip
                # para "ir al siguiente stop del mismo bus" sin re-bisectar por
                # parada (que es lo que hacía aparecer viajes imposiblemente
                # rápidos al saltar a trips distintos en cada hop).
                #
                # Almacenamos offsets en segundos relativos a la PRIMERA parada del
                # trip. El tiempo absoluto del trip se reconstruye con el dispatch
                # (hora real en que sale el bus, ver _expand_frequencies). Para
                # trips frequency-based, stop_times tiene tiempos relativos (el
                # primero típicamente 00:00); para trips estándar, los offsets
                # capturan la duración entre paradas y el dispatch recupera la
                # hora absoluta de la primera.
                sorted_st = sorted(stop_times, key=lambda s: s.stop_sequence)
                if not sorted_st:
                    continue
                first_secs = int(sorted_st[0].arrival_time.total_seconds())
                trip_seq = [
                    (st.stop_id, int(st.arrival_time.total_seconds()) - first_secs)
                    for st in sorted_st
                ]
                self.trips[trip.trip_id] = trip_seq
                self.trip_stop_idx[trip.trip_id] = {
                    sid: idx for idx, (sid, _) in enumerate(trip_seq)
                }
                self.trip_service[trip.trip_id] = service_id
                self.trip_route[trip.trip_id] = route.route_id
                # Default: un único dispatch a la hora absoluta de la primera parada.
                # _expand_frequencies sobreescribe esto para trips con frecuencias.
                self.trip_dispatches[trip.trip_id] = [first_secs]

                # Registrar paradas de la ruta (todas menos la última del trip:
                # la última no permite avanzar). Sin arrival_times: el CSA usa
                # trips_here + trip_dispatches; los viejos arrays de datetime.time
                # (millones de objetos, ordenados al arranque) no tenían lector.
                for st in sorted_st[:-1]:
                    stop_id = st.stop_id
                    if route.route_id not in stop_coords:
                        stop_coords[route.route_id] = {}
                    if stop_id in stop_coords[route.route_id]:
                        continue

                    stop = stops_by_id.get(stop_id)
                    if stop is None or stop.stop_lat is None or stop.stop_lon is None:
                        continue  # Saltar paradas sin coordenadas
                    try:
                        lat = float(stop.stop_lat)
                        lon = float(stop.stop_lon)
                    except (ValueError, TypeError):
                        continue  # Saltar paradas con coordenadas inválidas
                    # Validar rango geográfico
                    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                        continue

                    stop_coords[route.route_id][stop_id] = (lon, lat)
                    if route.route_id not in self.route_stops:
                        self.route_stops[route.route_id] = {}
                    self.route_stops[route.route_id][stop_id] = {
                        "route_id": route.route_id,
                        "stop_id": stop_id,
                        "coordinates": (lon, lat),
                        "sequence": st.stop_sequence,
                    }

        print("GTFS DATA RECEIVED SUCCESSFULLY")

    def get_stop_ids(self):
        stop_set = set()
        for route_id, stops in self.route_stops.items():
            for stop_id in stops:
                stop_set.add(stop_id)
        return stop_set

    def connection_finder(self, stop_id_1, stop_id_2):
        """
        Finds all routes that have stops at both given stop IDs.

        Parameters:
        stop_id_1 (str): The ID of the first stop to check.
        stop_id_2 (str): The ID of the second stop to check.

        Returns:
        list: A list of route IDs that have stops at both given stop IDs.
        """
        routes_1 = self._stop_to_routes.get(stop_id_1, ())
        routes_2 = set(self._stop_to_routes.get(stop_id_2, ()))
        return [route_id for route_id in routes_1 if route_id in routes_2]

    def get_routes_at_stop(self, stop_id: str) -> list:
        """
        Finds all routes that have a stop at the given stop ID.

        Parameters:
        stop_id (str): The ID of the stop to check.

        Returns:
        list: A list of route IDs that have a stop at the given stop ID.
        """
        return list(self._stop_to_routes.get(stop_id, []))

    # ────────────────────────────────────────────────────────────────────────
    # Líneas comunes (common lines) — opciones de recorrido sobre un mismo tramo
    # ────────────────────────────────────────────────────────────────────────

    def _next_departure_secs(
        self,
        route_id: str,
        stop_id: str,
        after_secs: int,
        active_services: frozenset = frozenset(),
    ) -> int | None:
        """Próxima salida (segundos desde medianoche) de ``route_id`` en
        ``stop_id`` en/después de ``after_secs``.

        Reusa la misma fuente que el CSA para abordajes: ``trips_here`` (trips de
        la ruta que pasan por la parada, con su offset) + ``trip_dispatches``
        (despachos absolutos expandidos de frequencies.txt) + ``trip_service``.
        Si ``active_services`` no está vacío, filtra por el ``service_id`` del
        trip (líneas que operan ese día). Devuelve ``None`` si la ruta no tiene
        ninguna salida válida desde esa parada (incluido el caso de que no haya
        datos de horario para ella).

        No aplica módulo a 24 h: los despachos son absolutos desde medianoche, lo
        que deja que una salida pasada la medianoche (p.ej. 24:10) siga siendo
        comparable contra una ventana nocturna sin envolver.
        """
        stops_dict = self.route_stops.get(route_id)
        if not stops_dict:
            return None
        stop_info = stops_dict.get(stop_id)
        if not stop_info:
            return None
        trips_here = stop_info.get("trips_here") or []
        if not trips_here:
            return None

        filter_svc = bool(active_services)
        best: int | None = None
        for trip_id, offset_secs in trips_here:
            if filter_svc:
                sid = self.trip_service.get(trip_id)
                if sid is None or sid not in active_services:
                    continue
            dispatches = self.trip_dispatches.get(trip_id)
            if not dispatches:
                continue
            for d in dispatches:
                abs_secs = d + offset_secs
                if abs_secs >= after_secs and (best is None or abs_secs < best):
                    best = abs_secs
        return best

    def common_lines_for_segment(
        self,
        route_id: str,
        leg_stops: list,
        departure_dt=None,
        *,
        within_minutes: float = 15.0,
        min_overlap: float = 0.5,
        respect_calendar: bool = True,
        max_options: int = 6,
    ) -> list[dict]:
        """Líneas que cubren el MISMO tramo que el segmento dado (mismos
        paraderos de subida/bajada y corredor compatible), como "opciones de
        recorrido" intercambiables.

        Implementa el patrón de **líneas comunes** (common lines / estrategias
        óptimas de Spiess & Florian): cuando varias líneas sirven un mismo
        tramo, desde el punto de vista del usuario son intercambiables ("toma la
        que llegue primero"), por lo que conviene presentarlas como un único
        viaje con varias opciones de recorrido en vez de itinerarios separados.

        **Criterio de corredor.** La subida y la bajada deben coincidir y la
        línea candidata debe recorrer el mismo corredor entre ambas, pero PUEDE
        saltarse paraderos intermedios (rutas expresas / variantes, comunes en
        Santiago). Se acepta si los paraderos que la candidata hace entre subida
        y bajada están contenidos en (o contienen a) los del tramo elegido con un
        solape ``>= min_overlap`` — nunca si introduce paraderos ajenos que
        delaten otra calle. Si alguno de los dos no tiene paraderos intermedios
        (tramo de 2 paradas, o expreso que se salta todo), basta con que la
        candidata recorra subida→bajada en ese orden.

        **Filtro de servicio/hora.** Si se pasa ``departure_dt``, sólo se listan
        líneas que operan ese día (``calendar.txt`` vía ``active_services_on``) y
        con una salida desde el paradero de subida dentro de
        ``[salida - 1 min, salida + within_minutes]``. Con ``departure_dt=None``
        no se filtra por hora (sólo estructura).

        Args:
            route_id: ruta del tramo elegido (se excluye del resultado).
            leg_stops: paraderos del tramo en orden ``[subida, ..., bajada]``.
            departure_dt: ``datetime`` de salida del tramo (para el filtro de
                hora). ``None`` → sin filtro temporal.
            within_minutes: ventana hacia adelante para considerar "un paso
                próximo".
            min_overlap: solape mínimo (contención) de paraderos intermedios.
            respect_calendar: filtrar por servicios activos en la fecha de salida.
            max_options: máximo de alternativas devueltas.

        Returns:
            Lista de dicts ``{route_id, route_short_name, route_long_name,
            route_color, mode, next_departure_secs}`` ordenada por próxima salida;
            ``[]`` si no hay líneas equivalentes. NO incluye ``route_id`` (la
            línea elegida); el llamador ya la conoce.
        """
        if not leg_stops or len(leg_stops) < 2:
            return []
        board, alight = leg_stops[0], leg_stops[-1]
        if board is None or alight is None or board == alight:
            return []
        inter_leg = {s for s in leg_stops[1:-1] if s is not None}

        candidates = [r for r in self.connection_finder(board, alight) if r != route_id]
        if not candidates:
            return []

        active: frozenset = frozenset()
        dep_secs: int | None = None
        window_hi: int | None = None
        if departure_dt is not None:
            t = departure_dt.time()
            dep_secs = t.hour * 3600 + t.minute * 60 + t.second
            window_hi = dep_secs + int(within_minutes * 60)
            if respect_calendar and hasattr(self, "active_services_on"):
                active = self.active_services_on(departure_dt.date())

        order_by_dir = self._route_dir_stop_order
        stops_by_dir = self._route_dir_stops_ordered

        options: list[dict] = []
        seen: set[str] = set()
        for cand in candidates:
            if cand in seen:
                continue
            seen.add(cand)

            # Dirección donde subida precede a bajada (menor span, como
            # get_route_stops_segment). Descarta el sentido contrario.
            best_dir = None
            best_span = None
            for direction in (0, 1):
                order = order_by_dir.get((cand, direction))
                if not order:
                    continue
                i_b = order.get(board)
                i_a = order.get(alight)
                if i_b is None or i_a is None or i_b >= i_a:
                    continue
                span = i_a - i_b
                if best_span is None or span < best_span:
                    best_span = span
                    best_dir = direction
            if best_dir is None:
                continue

            order = order_by_dir[(cand, best_dir)]
            stops_ordered = stops_by_dir.get((cand, best_dir)) or []
            i_b = order[board]
            i_a = order[alight]
            inter_cand = {s for s in stops_ordered[i_b + 1 : i_a] if s is not None}

            # Test de corredor por contención: con intermedios en ambos, exigir
            # solape sobre el menor; si alguno es vacío, basta el orden subida<bajada.
            if inter_leg and inter_cand:
                overlap = len(inter_leg & inter_cand) / min(len(inter_leg), len(inter_cand))
                if overlap < min_overlap:
                    continue

            # Filtro de servicio/hora (solo si hay departure_dt).
            next_dep: int | None = None
            if dep_secs is not None:
                next_dep = self._next_departure_secs(cand, board, dep_secs - 60, active)
                if next_dep is None:
                    # Sin paso válido en/después de la salida: si la ruta SÍ tiene
                    # horario aquí, su próximo bus ya pasó o cae fuera → no es
                    # alternativa. Si no hay datos de horario, ser indulgente
                    # (incluir) para no perder líneas por gaps del feed.
                    ti = self.route_stops.get(cand, {}).get(board, {})
                    if ti.get("trips_here"):
                        continue
                elif next_dep > window_hi:
                    continue

            meta = self.route_meta.get(cand) or {}
            options.append(
                {
                    "route_id": cand,
                    "route_short_name": meta.get("short_name"),
                    "route_long_name": meta.get("long_name"),
                    "route_color": meta.get("route_color"),
                    "mode": meta.get("mode", "bus"),
                    "next_departure_secs": next_dep,
                }
            )

        options.sort(
            key=lambda o: (
                o["next_departure_secs"] is None,
                o["next_departure_secs"] if o["next_departure_secs"] is not None else 0,
                o["route_short_name"] or o["route_id"],
            )
        )
        return options[:max_options]

    @staticmethod
    def haversine(lon1, lat1, lon2, lat2):
        """
        Calcula la distancia entre dos puntos usando la fórmula de Haversine.

        Parameters:
        lon1, lat1: Coordenadas del primer punto (longitud, latitud) en grados
        lon2, lat2: Coordenadas del segundo punto (longitud, latitud) en grados

        Returns:
        float: Distancia en kilómetros
        """
        lat1_rad = radians(lat1)
        lat2_rad = radians(lat2)
        half_delta_lat = radians(lat2 - lat1) * 0.5
        half_delta_lon = radians(lon2 - lon1) * 0.5

        a = sin(half_delta_lat) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(half_delta_lon) ** 2
        return _EARTH_RADIUS_KM * 2.0 * asin(sqrt(a))

    def get_nearby_stops(self, location_coords, margin_km=0.5, max_stops=10):
        """
        Finds stops within a given distance margin from a location.
        Optimized using cKDTree.

        Parameters:
        location_coords (tuple): A tuple with the location's coordinates (lat, lon).
        margin_km (float): The maximum distance in kilometers to search for stops. Default is 0.5 km.
        max_stops (int): Maximum number of stops to return. Default is 10.

        Returns:
        list: A list of tuples (stop_id, distance_km) sorted by distance, closest first.
        """
        if self._spatial_tree is None:
            return []

        lat, lon = location_coords

        # Aproximación: 1 grado latitud ~= 111 km
        # Usamos un margen ligeramente mayor en grados para asegurar que incluimos todos los puntos
        margin_deg = (margin_km / 111.0) * 1.2

        # Buscar índices de puntos candidatos en el árbol KD
        # query_ball_point usa distancia Euclidiana, que es una buena aproximación local
        indices = self._spatial_tree.query_ball_point([lat, lon], r=margin_deg)

        nearby_stops = []
        for idx in indices:
            stop_id = self._stop_ids_list[idx]
            s_lat, s_lon = self._stop_coords_array[idx]

            # Calcular distancia exacta con Haversine
            distance = self.haversine(lon, lat, s_lon, s_lat)

            if distance <= margin_km:
                nearby_stops.append((stop_id, distance))

        # Sort by distance (closest first)
        nearby_stops.sort(key=lambda x: x[1])

        # Return at most max_stops
        return nearby_stops[:max_stops]

    def get_stop_coords(self, stop_id: str):
        """
        Obtiene las coordenadas (lon, lat) de una parada.

        Args:
            stop_id: ID de la parada

        Returns:
            tuple: (lon, lat) o None si no existe la parada
        """
        # _stop_coords se precomputa al arranque (en _build_route_index) cubriendo
        # tanto las paradas de rutas como las sueltas del feed, así que basta el
        # lookup en memoria — no se consulta el scheduler SQLite en tiempo de
        # request (ver nota de hilos en _build_route_index).
        return self._stop_coords.get(stop_id)

    def find_nearby_routes(self, stop_id: str, margin_km: float = 0.5):
        """
        Encuentra otras rutas con paradas cercanas a una parada dada.

        Args:
            stop_id: ID de la parada de referencia
            margin_km: Radio de búsqueda en kilómetros (default: 0.5 km)

        Returns:
            dict: {route_id: [(nearby_stop_id, distance_km), ...]}
        """
        stop_coords = self.get_stop_coords(stop_id)
        if stop_coords is None:
            return {}

        nearby_stops = self.get_nearby_stops(
            (stop_coords[1], stop_coords[0]),  # get_nearby_stops espera (lat, lon)
            margin_km=margin_km,
            max_stops=50,
        )

        # Agrupar por ruta usando el índice stop→rutas (O(1) por parada vecina)
        routes_nearby: dict = {}
        for nearby_stop_id, distance in nearby_stops:
            if nearby_stop_id == stop_id:
                continue
            for route_id in self._stop_to_routes.get(nearby_stop_id, ()):
                routes_nearby.setdefault(route_id, []).append((nearby_stop_id, distance))

        # Las paradas ya vienen ordenadas por distancia desde get_nearby_stops,
        # así que las listas por ruta también heredan el orden correcto.
        return routes_nearby

    def compute_all_transfers(
        self,
        max_distance_km: float = 0.5,
        max_waiting_minutes: int = 15,
        walking_speed_kmh: float = 5.0,
        osm_graph=None,
        osm_min_dist_km: float = 0.03,
    ):
        """
        Calcula todas las transferencias posibles entre rutas.

        Args:
            max_distance_km: Distancia máxima de caminata para transbordo (default: 0.5 km)
            max_waiting_minutes: Tiempo máximo de espera (default: 15 minutos)
            walking_speed_kmh: Velocidad de caminata (default: 5 km/h)

        Returns:
            TransferManager: Objeto con todas las transferencias calculadas
        """
        from .TransferConnection import TransferConnection, TransferManager

        transfer_manager = TransferManager()
        transfer_count = 0

        print(f"Calculando transferencias para {len(self.route_stops)} rutas...")

        # Para cada ruta
        for from_route_id, stops_dict in self.route_stops.items():
            # Para cada parada de la ruta
            for from_stop_id in stops_dict.keys():
                # Encontrar rutas cercanas
                nearby_routes = self.find_nearby_routes(from_stop_id, margin_km=max_distance_km)

                # Crear transferencias
                for to_route_id, nearby_stops in nearby_routes.items():
                    # Evitar transferencias a la misma ruta
                    if from_route_id == to_route_id:
                        continue

                    # Para cada parada cercana de la ruta destino
                    for to_stop_id, distance in nearby_stops[:3]:  # Top 3 más cercanas
                        walking_path = None
                        # Distancia/geometría peatonal real (OSM) si está disponible.
                        # Se omite para pares casi coincidentes (< osm_min_dist_km):
                        # OSM no aporta y es la mayor parte de las ~1.3M llamadas.
                        if (
                            osm_graph is not None
                            and from_stop_id != to_stop_id
                            and distance >= osm_min_dist_km
                        ):
                            fc = self.get_stop_coords(from_stop_id)
                            tc = self.get_stop_coords(to_stop_id)
                            if fc is not None and tc is not None:
                                try:
                                    res = osm_graph.shortest_path(
                                        (fc[1], fc[0]), (tc[1], tc[0])
                                    )
                                except Exception:
                                    res = None
                                if res and not (
                                    distance > 0 and res[0] > distance * 3
                                ):
                                    distance, walking_path = res

                        # Calcular tiempo de caminata
                        walking_time = (distance / walking_speed_kmh) * 3600  # segundos

                        # Determinar tipo de transbordo
                        if from_stop_id == to_stop_id:
                            transfer_type = "same_stop"
                        elif distance < 0.05:  # Menos de 50 metros
                            transfer_type = "nearby"
                        else:
                            transfer_type = "walking"

                        # Crear transferencia
                        transfer = TransferConnection(
                            from_route_id=from_route_id,
                            to_route_id=to_route_id,
                            from_stop_id=from_stop_id,
                            to_stop_id=to_stop_id,
                            walking_distance_km=distance,
                            walking_time_seconds=walking_time,
                            min_transfer_time=max(120, int(walking_time)),  # Mínimo 2 minutos
                            max_waiting_time=max_waiting_minutes * 60,
                            transfer_type=transfer_type,
                            walking_path=walking_path,
                        )

                        transfer_manager.add_transfer(transfer)
                        transfer_count += 1

        # Almacenar en la instancia
        self.transfer_manager = transfer_manager

        return transfer_manager

    def _validate_transfer_cache(self, tm) -> None:
        """Verifica que el cache de transferencias corresponda al feed actual.

        Muestrea stop_ids de las keys del TransferManager y exige que la
        mayoría estén presentes en ``self.stops``. Si el feed cambió de
        versión los stop_ids viejos no existen y los transbordos cargados
        son basura — mejor recomputar que servir transbordos huérfanos.
        Lanza ``ValueError`` si el cache está obsoleto.

        El chequeo global no basta: un cambio de ids que afecte sólo a un modo
        minoritario (p.ej. el Metro: ~150 de ~12k paradas) no mueve el umbral
        global del 10% porque los ~11k paraderos de bus —estables entre
        versiones— lo dominan. El resultado es que TODOS los footpaths
        bus↔metro quedan huérfanos (su ``to_stop`` ya no existe) y el Metro
        desaparece silenciosamente de los viajes. Por eso, además del umbral
        global, validamos cobertura **por modo** sobre las paradas de origen y
        destino del cache.
        """
        feed_stops = self.stops if isinstance(self.stops, set) else set(self.stops)
        if not feed_stops:
            return
        cache_stops = {key[1] for key in tm.transfers.keys()}
        if not cache_stops:
            return
        unknown = cache_stops - feed_stops
        if len(unknown) / len(cache_stops) > 0.10:
            raise ValueError(
                f"{len(unknown)}/{len(cache_stops)} stop_ids del cache no están "
                f"en el feed actual (probable cambio de versión)"
            )

        # Cobertura por modo: agrupamos las paradas del cache por el modo de la
        # ruta que las usa (origen vía from_route, destino vía to_route) y
        # exigimos que cada modo siga existiendo en el feed. Si casi todas las
        # paradas de un modo desaparecieron, sus transbordos son huérfanos →
        # recomputar aunque el global pase.
        mode_stops: dict[str, set] = defaultdict(set)
        for (from_route, from_stop), conns in tm.transfers.items():
            mode_stops[self.get_route_mode(from_route)].add(from_stop)
            for t in conns:
                mode_stops[self.get_route_mode(t.to_route_id)].add(t.to_stop_id)
        for mode, stops in mode_stops.items():
            if len(stops) < 20:
                continue  # muestra demasiado chica para un veredicto robusto
            unknown_m = stops - feed_stops
            if len(unknown_m) / len(stops) > 0.50:
                raise ValueError(
                    f"modo '{mode}': {len(unknown_m)}/{len(stops)} paradas del "
                    f"cache no están en el feed actual (cambio de ids del feed "
                    f"para ese modo; sus transbordos quedarían huérfanos)"
                )

    def get_or_compute_transfers(
        self,
        cache_path: str = None,
        max_distance_km: float = 0.5,
        walking_speed_kmh: float = 5.0,
        osm_graph=None,
    ):
        """
        Retorna el TransferManager precalculado, cargándolo desde cache si existe.
        Si no hay cache (o está corrupto), lo calcula y lo guarda.

        Args:
            cache_path: Ruta al archivo JSON de cache (None = no persistir)
            max_distance_km: Distancia máxima de caminata para transbordos
            walking_speed_kmh: Velocidad de caminata en km/h

        Returns:
            TransferManager con todas las transferencias viables
        """
        import os

        from .TransferConnection import TransferManager

        if hasattr(self, "transfer_manager"):
            return self.transfer_manager

        if cache_path and os.path.exists(cache_path):
            try:
                tm = TransferManager.load(cache_path)
                # Validación de coherencia feed↔cache: si el feed cambió de
                # versión (ej: 2023 → 2026), muchos stop_ids del cache ya no
                # existen y los transbordos quedarían huérfanos. Tomamos un
                # sample y exigimos que ≥ 90% de las paradas del cache estén
                # en el feed actual; si no, recomputamos.
                self._validate_transfer_cache(tm)
                self.transfer_manager = tm
                print(f"Transferencias cargadas desde cache: {tm.count_transfers()} registros.")
                return tm
            except Exception as e:
                print(f"Cache de transferencias inválido ({e}), recalculando...")

        tm = self.compute_all_transfers(
            max_distance_km=max_distance_km,
            walking_speed_kmh=walking_speed_kmh,
            osm_graph=osm_graph,
        )

        if cache_path:
            try:
                tm.save(cache_path)
                print(f"Cache de transferencias guardado en: {cache_path}")
            except Exception as e:
                print(f"No se pudo guardar cache de transferencias: {e}")

        return tm

