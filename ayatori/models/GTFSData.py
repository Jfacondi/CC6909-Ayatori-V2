import csv
import io
import json
import logging
import os
import warnings
import zipfile
from collections import defaultdict
from datetime import datetime, time, timedelta
from math import asin, cos, radians, sin, sqrt

import folium
import numpy as np
import pandas as pd
import pygtfs
import rustworkx as rx
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
        route_stops: ``{route_id: {stop_id: {sequence, coordinates, arrival_times}}}``.
        stops: conjunto de stop_ids vistos en alguna ruta.
        special_dates: feriados / excepciones del calendar.
        graphs: ``{route_id: rustworkx.PyDiGraph}`` por ruta.
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
        self.graphs = {}
        self._graph_node_maps = {}  # {route_id: {stop_id: rx_idx}}
        self._graph_idx_to_node = {}  # {route_id: {rx_idx: stop_id}}
        self.route_stops = {}
        self.special_dates = []
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
        self.graphs, self.route_stops, self.special_dates = self.get_gtfs_data()
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

    @staticmethod
    def _orientation_from_trip(trip) -> str:
        """Orientación canónica ("I" ida / "R" retorno) de un trip.

        Prefiere ``direction_id`` (columna GTFS estándar, 0=outbound, 1=inbound).
        Cae al parse legacy ``trip_id.split("-")[1]`` del feed antiguo de Red
        Metropolitana (formato ``XXX-{I|R}-Z-BNN``) solo si direction_id no
        viene. Los trips de Metro del feed 2026 usan ``DF_L1_..._V1`` sin
        dashes y solo son ruteables vía direction_id.
        """
        dir_id = getattr(trip, "direction_id", None)
        if dir_id is not None and dir_id != "":
            try:
                return "I" if int(dir_id) == 0 else "R"
            except (ValueError, TypeError):
                pass
        parts = (getattr(trip, "trip_id", None) or "").split("-")
        if len(parts) >= 2:
            return parts[1]
        return "I"

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

            dispatches = expanded.setdefault(tid, [])
            t = start
            while t < end:
                dispatches.append(t)
                t += headway

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
          - arrival_times de cada parada se pre-ordenan una sola vez aquí
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
                arrival_times = stop_info.get("arrival_times")
                if arrival_times:
                    arrival_times.sort()
                by_service = stop_info.get("arrival_times_by_service")
                if by_service:
                    for times in by_service.values():
                        times.sort()
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

    def get_stop_name(self, stop_id: str) -> str | None:
        """Nombre legible de una parada, o None si no se conoce."""
        return self.stop_names.get(stop_id)

    def _build_calendar_index(self):
        """Indexa calendar.txt + calendar_dates.txt para filtrar por fecha.

        - ``self.services[service_id] = {"weekdays": (lun..dom bool), "start", "end"}``
        - ``self.service_exceptions[service_id] = [(date, exception_type), ...]``
          donde exception_type 1=agrega servicio, 2=lo quita ese día.

        ``special_dates`` se mantiene intacto (compat de ``is_holiday``).
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

        return shape[idx_from : idx_to + 1].tolist()

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
            for a, b in zip(coords[:-1], coords[1:]):
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
            scheduler = pygtfs.Schedule(":memory:")
            pygtfs.append_feed(scheduler, str(gtfs_to_use))
            return scheduler
        except (TypeError, ValueError) as e:
            # Fallback: si aún falla por coordenadas None, reintentar con cleaner
            # (cubre el caso en que la pre-limpieza no se ejecutó por algún motivo).
            if "float()" in str(e) and "NoneType" in str(e):
                try:
                    gtfs_to_use = clean_gtfs_stops(GTFS_PATH)
                    scheduler = pygtfs.Schedule(":memory:")
                    pygtfs.append_feed(scheduler, str(gtfs_to_use))
                    return scheduler
                except Exception:
                    raise
            else:
                raise

    def get_gtfs_data(self):
        """
        Reads the GTFS data from a file and creates a directed graph with its info, using the 'pygtfs' library. This gives
        the transit feed data of Santiago's public transport, including "Red Metropolitana de Movilidad" (previously known
        as Transantiago), "Metro de Santiago", "EFE Trenes de Chile", and "Buses de Acercamiento Aeropuerto".

        Returns:
            graphs: GTFS data converted to a dictionary of graphs, one per route.
            route_stops: Dictionary containing the stops for each route.
            special_dates: List of special calendar dates.
        """
        sched = self.scheduler

        # Get special calendar dates
        for cal_date in sched.service_exceptions:  # Calendar_dates is renamed in pygtfs
            self.special_dates.append(cal_date.date.strftime("%d/%m/%Y"))

        stop_id_map = {}  # To assign unique ids to every stop
        stop_coords = {}

        for route in sched.routes:
            graph = rx.PyDiGraph()
            node_map = {}  # {stop_id: rx_idx}
            idx_to_node = {}  # {rx_idx: stop_id}
            stop_ids = set()
            trips = [trip for trip in sched.trips if trip.route_id == route.route_id]

            added_edges = set()  # To keep track of the edges that have already been added

            for trip in trips:
                stop_times = trip.stop_times
                orientation = self._orientation_from_trip(trip)
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

                for i in range(len(stop_times)):
                    stop_id = stop_times[i].stop_id
                    sequence = stop_times[i].stop_sequence

                    if stop_id not in stop_id_map:
                        vertex = stop_id  # Use stop_id directly as node identifier
                        stop_id_map[stop_id] = vertex
                    else:
                        vertex = stop_id_map[stop_id]

                    stop_ids.add(vertex)
                    # Add node to graph if it doesn't exist
                    if vertex not in node_map:
                        idx = graph.add_node({"stop_id": stop_id})
                        node_map[vertex] = idx
                        idx_to_node[idx] = vertex

                    if i < len(stop_times) - 1:
                        next_stop_id = stop_times[i + 1].stop_id

                        if next_stop_id not in stop_id_map:
                            next_vertex = next_stop_id
                            stop_id_map[next_stop_id] = next_vertex
                        else:
                            next_vertex = stop_id_map[next_stop_id]

                        edge = (vertex, next_vertex)
                        if edge not in added_edges:  # Check if the edge has already been added
                            if next_vertex not in node_map:
                                next_idx = graph.add_node({"stop_id": next_stop_id})
                                node_map[next_vertex] = next_idx
                                idx_to_node[next_idx] = next_vertex
                            graph.add_edge(
                                node_map[vertex],
                                node_map[next_vertex],
                                {"weight": 1, "u": vertex, "v": next_vertex},
                            )
                            added_edges.add(edge)  # Add the edge to the set of added edges

                        if route.route_id not in stop_coords:
                            stop_coords[route.route_id] = {}

                        if stop_id not in stop_coords[route.route_id]:
                            stop = sched.stops_by_id(stop_id)[0]

                            # Validar que la parada tiene coordenadas válidas
                            if stop.stop_lat is None or stop.stop_lon is None:
                                continue  # Saltar paradas sin coordenadas

                            try:
                                lat = float(stop.stop_lat)
                                lon = float(stop.stop_lon)

                                # Validar rango geográfico
                                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                                    continue

                                stop_coords[route.route_id][stop_id] = (lon, lat)
                            except (ValueError, TypeError):
                                continue  # Saltar paradas con coordenadas inválidas

                            if route.route_id not in self.route_stops:
                                self.route_stops[route.route_id] = {}

                            self.route_stops[route.route_id][stop_id] = {
                                "route_id": route.route_id,
                                "stop_id": stop_id,
                                "coordinates": stop_coords[route.route_id][stop_id],
                                "orientation": "round" if orientation == "I" else "return",
                                "sequence": sequence,
                                "arrival_times": [],
                                "arrival_times_by_service": defaultdict(list),
                            }

                    arrival_time = (datetime.min + stop_times[i].arrival_time).time()

                    # Solo agregar tiempo de llegada si la parada es válida
                    if stop_id in self.route_stops.get(route.route_id, {}):
                        entry = self.route_stops[route.route_id][stop_id]
                        entry["arrival_times"].append(arrival_time)
                        if service_id is not None:
                            entry["arrival_times_by_service"][service_id].append(
                                arrival_time
                            )

            self.graphs[route.route_id] = graph
            self._graph_node_maps[route.route_id] = node_map
            self._graph_idx_to_node[route.route_id] = idx_to_node

            stops_by_direction = {"round_trip": [], "return_trip": []}
            for trip in trips:
                stop_times = trip.stop_times
                stops = [stop_times[i].stop_id for i in range(len(stop_times))]

                if trip.direction_id == 0:
                    stops_by_direction["round_trip"].extend(stops)
                else:
                    stops_by_direction["return_trip"].extend(stops)

            round_trip_stops = set(stops_by_direction["round_trip"])
            return_trip_stops = set(stops_by_direction["return_trip"])

            for stop_id in round_trip_stops:
                if stop_id in stop_coords[route.route_id]:
                    if stop_id in self.route_stops[route.route_id]:
                        self.route_stops[route.route_id][stop_id]["orientation"] = "round"
                    else:
                        self.route_stops[route.route_id][stop_id] = {
                            "route_id": route.route_id,
                            "stop_id": stop_id,
                            "coordinates": stop_coords[route.route_id][stop_id],
                            "orientation": "round",
                            "sequence": sequence,
                            "arrival_times": [],
                            "arrival_times_by_service": defaultdict(list),
                        }

            for stop_id in return_trip_stops:
                if stop_id in stop_coords[route.route_id]:
                    if stop_id in self.route_stops[route.route_id]:
                        self.route_stops[route.route_id][stop_id]["orientation"] = "return"
                    else:
                        self.route_stops[route.route_id][stop_id] = {
                            "route_id": route.route_id,
                            "stop_id": stop_id,
                            "coordinates": stop_coords[route.route_id][stop_id],
                            "orientation": "return",
                            "sequence": sequence,
                            "arrival_times": [],
                            "arrival_times_by_service": defaultdict(list),
                        }

        for route_id, graph in self.graphs.items():
            data_dir = "gtfs_routes"
            if not os.path.exists(data_dir):
                os.makedirs(data_dir)

            # graph.save(f"{data_dir}/{route_id}.gt")  # Legacy: graph-tool method (not available in networkx)

        print("GTFS DATA RECEIVED SUCCESSFULLY")

        return self.graphs, self.route_stops, self.special_dates

    def get_stop_ids(self):
        stop_set = set()
        for route_id, stops in self.route_stops.items():
            for stop_id in stops:
                stop_set.add(stop_id)
        return stop_set

    def get_route_graph(self, route_id):
        """
        Given a route_id, returns the vertices and edges for the corresponding graph.

        Parameters:
        route_id (str): The ID of the route.

        Returns:
        tuple: A tuple containing the vertices and edges of the graph. The vertices are a list of node IDs, and the edges are a list of tuples containing the source and target node IDs.
        """
        if route_id not in self.graphs:
            print(f"Route {route_id} does not exist.")
            return None

        graph = self.graphs[route_id]
        idx_to_node = self._graph_idx_to_node[route_id]

        vertices = []
        for idx in graph.node_indices():
            node_id = idx_to_node[idx]
            if node_id != "" and node_id is not None:
                vertices.append(node_id)

        edges = []
        for u_idx, v_idx in graph.edge_list():
            u = idx_to_node[u_idx]
            v = idx_to_node[v_idx]
            if u is not None and v is not None:
                edges.append((u, v))

        return vertices, edges

    def get_route_graph_vertices(self, route_id):
        """
        Given a route_id, returns the vertices for the corresponding graph.

        Parameters:
        route_id (str): The ID of the route.

        Returns:
        list: A list containing the vertices of the graph. The vertices are a list of node IDs.
        """
        if route_id not in self.graphs:
            print(f"Route {route_id} does not exist.")
            return None

        graph = self.graphs[route_id]
        idx_to_node = self._graph_idx_to_node[route_id]
        vertices = [idx_to_node[idx] for idx in graph.node_indices()]

        return vertices

    def get_route_graph_edges(self, route_id):
        """
        Given a route_id, returns the edges for the corresponding graph.

        Parameters:
        route_id (str): The ID of the route.

        Returns:
        list: A list containing the edges of the graph.
        """
        if route_id not in self.graphs:
            print(f"Route {route_id} does not exist.")
            return None

        graph = self.graphs[route_id]
        idx_to_node = self._graph_idx_to_node[route_id]
        edges = [(idx_to_node[u], idx_to_node[v]) for u, v in graph.edge_list()]

        return edges

    def map_route_stops(self, route_list, stops_flag, orientation_flag):
        """
        Create a map showing the stops visited on the round trip for the specified routes.

        Parameters:
        route_list (list): A list of route IDs.
        stops_flag (bool): A flag indicating whether to display the stops on the map.

        Returns:
        folium.Map: A map object showing the stops and routes.
        """
        # Map the stops visited on the round trip
        map = folium.Map(location=[-33.45, -70.65], zoom_start=12)

        # List of valid colors
        map_colors = [
            "red",
            "orange",
            "darkred",
            "blue",
            "lightblue",
            "green",
            "purple",
            "lightred",
            "beige",
            "darkblue",
            "darkgreen",
            "cadetblue",
            "darkpurple",
            "white",
            "pink",
            "lightgreen",
            "gray",
            "black",
            "lightgray",
        ]

        color_id = 0
        for route_id in route_list:
            # Get the stops for the specified route
            stops = self.route_stops.get(route_id, {})

            # Filter the stops that are visited on the round trip
            if orientation_flag:
                trip_stops = [
                    stop_info for stop_info in stops.values() if stop_info["orientation"] == "round"
                ]
            else:
                trip_stops = [
                    stop_info
                    for stop_info in stops.values()
                    if stop_info["orientation"] == "return"
                ]

            # Sort the stops by their sequence number in the trip
            trip_stops = sorted(trip_stops, key=lambda x: x["sequence"])

            folium.PolyLine(
                locations=[
                    [stop_info["coordinates"][1], stop_info["coordinates"][0]]
                    for stop_info in trip_stops
                ],
                color=map_colors[color_id],
                weight=4,
            ).add_to(map)

            if stops_flag:
                for stop_info in trip_stops:
                    folium.Marker(
                        location=[stop_info["coordinates"][1], stop_info["coordinates"][0]],
                        popup=stop_info["stop_id"],
                        icon=folium.Icon(color="lightgray", icon="minus"),
                    ).add_to(map)

            color_id += 1

        return map

    def get_route_coordinates(self, route_id):
        round_trip_stops = []
        return_trip_stops = []
        for stop_info in self.route_stops[route_id].values():
            if stop_info["orientation"] == "round":
                round_trip_stops.append(stop_info)
            elif stop_info["orientation"] == "return":
                return_trip_stops.append(stop_info)

        round_trip_stops.sort(key=lambda stop: stop["sequence"])
        return_trip_stops.sort(key=lambda stop: stop["sequence"])

        round_trip_coords = [
            (stop_info["coordinates"][1], stop_info["coordinates"][0])
            for stop_info in round_trip_stops
        ]
        return_trip_coords = [
            (stop_info["coordinates"][1], stop_info["coordinates"][0])
            for stop_info in return_trip_stops
        ]

        return round_trip_coords, return_trip_coords

    def get_near_stop_ids(self, coords, margin):
        """
        Given a tuple of coordinates and a margin, returns a list of stop IDs
        that are within the specified margin of the given coordinates, along with their orientations.

        Parameters:
        coords (tuple): (lon, lat) of the search center.
        margin (float): Maximum distance in kilometers.

        Returns:
        tuple: ([stop_id, ...], [(stop_id, orientation), ...])
        """
        lon, lat = coords
        nearby = self.get_nearby_stops((lat, lon), margin_km=margin)

        stop_ids = []
        orientations = []
        seen = set()
        for stop_id, _ in nearby:
            if stop_id in seen:
                continue
            seen.add(stop_id)
            stop_ids.append(stop_id)
            orientation = None
            for stops_dict in self.route_stops.values():
                if stop_id in stops_dict:
                    orientation = stops_dict[stop_id]["orientation"]
                    break
            orientations.append((stop_id, orientation))
        return stop_ids, orientations

    def get_route_stop_ids(self, route_id):
        """
        Given a route ID, returns a list of stop IDs for the stops on the given route.

        Parameters:
        route_id (int): The ID of the route to get the stops for.

        Returns:
        list: A list of stop IDs for the stops on the given route.
        """
        return list(self.route_stops.get(route_id, {}).keys())

    def route_stop_matcher(self, route_id, stop_id):
        """
        Given a route ID, and a stop ID, returns True if the stop ID is on the given route,
        and False otherwise.

        Parameters:
        route_id (int): The ID of the route to check.
        stop_id (int): The ID of the stop to check.

        Returns:
        bool: True if the stop ID is on the given route, False otherwise.
        """
        stop_list = self.get_route_stop_ids(route_id)
        return stop_id in stop_list

    def is_route_near_coordinates(self, route_id, coordinates, margin):
        """
        Given a route ID, a tuple of coordinates, and a margin, returns True if the route
        has a stop within the specified margin of the given coordinates, and False otherwise.

        Parameters:
        route_id (int): The ID of the route to check.
        coordinates (tuple): A tuple of two floats representing the longitude and latitude of the coordinates to search around.
        margin (float): The maximum distance (in kilometers) from the given coordinates to include stops in the result.

        Returns:
        bool: True if the route has a stop within the specified margin of the given coordinates, False otherwise.
        """
        for stop_info in self.route_stops[route_id].values():
            stop_coords = stop_info["coordinates"]
            distance = self.haversine(
                coordinates[1], coordinates[0], stop_coords[1], stop_coords[0]
            )
            if distance <= margin:
                return route_id
        return False

    def get_bus_orientation(self, route_id, stop_id):
        """
        Checks and confirms the bus orientation, while visiting a stop, in the GTFS data files.

        Parameters:
        route_id (str): The route or service's ID to check.
        stop_id (str): The visited stop ID.

        Returns:
        str or list: The bus orientation(s) associated with the route_id and stop_id. None if nothing is found.
        """
        stop_times = pd.read_csv("stop_times.txt")
        filtered_stop_times = stop_times[
            (stop_times["trip_id"].str.startswith(route_id)) & (stop_times["stop_id"] == stop_id)
        ]

        orientations = []
        for trip_id in filtered_stop_times["trip_id"]:
            # Fallback al parse legacy del trip_id; el feed 2026 usa
            # direction_id pero esta función legacy no recibe el objeto
            # trip de pygtfs, sólo el trip_id, así que no puede leerlo.
            parts = str(trip_id).split("-")
            if len(parts) < 2:
                continue
            orientation = parts[1]
            if orientation == "I" and "round" not in orientations:
                orientations.append("round")
            elif orientation == "R" and "return" not in orientations:
                orientations.append("return")

        if len(orientations) == 0:
            return None
        elif len(set(orientations)) == 1:
            return orientations[0]
        else:
            return orientations

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

    def is_24_hour_service(self, route_id):
        """
        Determines if the given route has a 24-hour service.

        Parameters:
        route_id (str): A string representing the ID of the route.

        Returns:
        bool: True if the route has a 24-hour service, False otherwise.
        """
        # Read the frequencies for the route
        frequencies = pd.read_csv("frequencies.txt")
        route_str = str(route_id) + "-"
        route_frequencies = frequencies[frequencies["trip_id"].str.startswith(route_str)]

        # Check if any frequency has a start time of "00:00:00" and an end time of "24:00:00"
        has_start_time = False
        has_end_time = False
        for _, row in route_frequencies.iterrows():
            start_time = row["start_time"]
            end_time = row["end_time"]
            if start_time == "00:00:00":
                has_start_time = True
            if end_time == "24:00:00":
                has_end_time = True

        return has_start_time and has_end_time

    def check_night_routes(self, valid_services, is_nighttime):
        """
        Filters the given list of route IDs to only include night routes if is_nighttime is True.

        Parameters:
        valid_services (list): A list of route IDs to filter.
        is_nighttime (bool): True if it is nighttime, False otherwise.

        Returns:
        list: A list of route IDs that are night routes if is_nighttime is True, or all route IDs otherwise.
        """
        if is_nighttime:
            # nighttime_routes = [route_id for route_id in valid_services if route_id.endswith("N")]
            nighttime_routes = [
                route_id
                for route_id in valid_services
                if route_id.endswith("N") or self.is_24_hour_service(route_id)
            ]
            if nighttime_routes:
                return nighttime_routes
            else:
                return None
        else:
            daytime_routes = [route_id for route_id in valid_services if not route_id.endswith("N")]
            if daytime_routes:
                return daytime_routes
            else:
                return None

    def is_nighttime(self, source_hour):
        """
        Determines if the given hour is during the nighttime.

        Parameters:
        source_hour (datetime.time): The hour to check.

        Returns:
        bool: True if the hour is during the nighttime, False otherwise.
        """
        start_time = time(0, 0, 0)
        end_time = time(5, 30, 0)
        if start_time <= source_hour <= end_time:
            return True
        else:
            return False

    def is_holiday(self, date_string):
        """
        Checks if a given date is a holiday.

        Parameters:
        date_string (str): A string representing the date in the format "dd/mm/yyyy".

        Returns:
        bool: True if the date is a holiday, False otherwise.
        """
        # Local holidays
        if date_string in self.special_dates:
            return True
        date_obj = datetime.strptime(date_string, "%d/%m/%Y")

        # Weekend days
        day_of_week = date_obj.weekday()
        if day_of_week == 5 or day_of_week == 6:
            return True
        return False

    def is_rush_hour(self, source_hour):
        """
        Determines if the given hour is during rush hour.

        Parameters:
        source_hour (datetime.time): The hour to check.

        Returns:
        bool: True if the hour is during rush hour, False otherwise.
        """
        am_start_time = time(5, 30, 0)
        am_end_time = time(9, 0, 0)
        pm_start_time = time(17, 30, 0)
        pm_end_time = time(21, 0, 0)
        if (
            am_start_time <= source_hour <= am_end_time
            or pm_start_time <= source_hour <= pm_end_time
        ):
            return True
        else:
            return False

    def check_express_routes(self, valid_services, is_rush_hour):
        """
        Filters the given list of route IDs to only include express routes if is_rush_hour is True.

        Parameters:
        valid_services (list): A list of route IDs to filter.
        is_rush_hour (bool): True if it is rush hour, False otherwise.

        Returns:
        list: A list of route IDs that are express routes if is_rush_hour is True, or all route IDs otherwise.
        """
        if is_rush_hour:
            return valid_services
        else:
            regular_hour_routes = [
                route_id for route_id in valid_services if not route_id.endswith("e")
            ]
            return regular_hour_routes

    def get_trip_day_suffix(self, date):
        """
        Based on the given date, gets the corresponding trip day suffix for the trip IDs.

        Parameters:
        date (date): The date to be checked.

        Returns
        str: A string with the trip day suffix.
        """
        date_object = datetime.strptime(date, "%d/%m/%Y")
        day_of_week = date_object.weekday()

        if day_of_week < 5:
            trip_day_suffix = "L"
        elif day_of_week == 5:
            trip_day_suffix = "S"
        else:
            trip_day_suffix = "D"

        return trip_day_suffix

    def get_arrival_times(self, route_id, stop_id, source_date):
        """
        Returns the arrival times for a given route and stop.

        Parameters:
        route_id (str): A string representing the ID of the route.
        stop_id (str): A string representing the ID of the stop.
        source_date (str): A string representing the date of the travel.

        Returns:
        tuple: A tuple containing a string representing the bus orientation ("round" or "return") and a list of datetime objects representing the arrival times.
        """
        # Read the frequencies.txt file
        frequencies = pd.read_csv("frequencies.txt")

        # Filter the frequencies for the given route ID
        route_frequencies = frequencies[frequencies["trip_id"].str.startswith(route_id)]

        # Get the day suffix
        day_suffix = self.get_trip_day_suffix(source_date)

        # Get the arrival times for the stop for each trip
        stop_route_times = []
        bus_orientation = ""
        for _, row in route_frequencies.iterrows():
            start_time = pd.Timestamp(row["start_time"])
            if row["end_time"] == "24:00:00":
                end_time = pd.Timestamp("23:59:59")
            else:
                end_time = pd.Timestamp(row["end_time"])
            headway_secs = row["headway_secs"]
            round_trip_id = f"{route_id}-I-{day_suffix}"
            return_trip_id = f"{route_id}-R-{day_suffix}"
            round_stop_times = pd.read_csv("stop_times.txt").query(
                f"trip_id.str.startswith('{round_trip_id}') and stop_id == '{stop_id}'"
            )
            return_stop_times = pd.read_csv("stop_times.txt").query(
                f"trip_id.str.startswith('{return_trip_id}') and stop_id == '{stop_id}'"
            )
            if len(round_stop_times) == 0 and len(return_stop_times) == 0:
                return
            elif len(round_stop_times) > 0:
                bus_orientation = "round"
                stop_time = pd.Timestamp(round_stop_times.iloc[0]["arrival_time"])
            elif len(return_stop_times) > 0:
                bus_orientation = "return"
                stop_time = pd.Timestamp(return_stop_times.iloc[0]["arrival_time"])
            for freq_time in pd.date_range(start_time, end_time, freq=f"{headway_secs}s"):
                freq_time_str = freq_time.strftime("%H:%M:%S")
                freq_time = datetime.strptime(freq_time_str, "%H:%M:%S")
                stop_route_time = datetime.combine(datetime.min, stop_time.time()) + timedelta(
                    seconds=(freq_time - datetime.min).seconds
                )
                if stop_route_time not in stop_route_times:
                    stop_route_times.append(stop_route_time)
                stop_time += pd.Timedelta(seconds=headway_secs)

        return bus_orientation, stop_route_times

    def get_time_until_next_bus(self, arrival_times, source_hour, source_date):
        """
        Returns the time until the next three buses.

        Parameters:
        arrival_times (list): A list of datetime objects representing the arrival times of the buses.
        source_hour (datetime.time): The source hour to compare with the arrival times.
        source_date (datetime.date): The source date to check if there are buses remaining.

        Returns:
        list: A list of tuples representing the time until the next three buses in minutes and seconds.
        """
        arrival_times_remaining = []
        for a_time in arrival_times:
            if a_time.time() >= source_hour:
                arrival_times_remaining.append(a_time)
        # arrival_times_remaining = [time for time in arrival_times if time.time() >= source_hour]
        if len(arrival_times_remaining) == 0:
            return None
        else:
            # Sort the remaining arrival times in ascending order
            arrival_times_remaining.sort()

            # Get the datetime objects for the next three buses
            next_buses = []
            for i in range(min(3, len(arrival_times_remaining))):
                next_arrival_time = arrival_times_remaining[i]
                next_bus = datetime.combine(next_arrival_time.date(), next_arrival_time.time())
                next_buses.append(next_bus)

            if next_buses is None:
                print("No buses remaining for the specified date.")
            else:
                # Calculate the time until the next three buses
                time_until_next_buses = []
                for next_bus in next_buses:
                    time_until_next_bus = (
                        next_bus - datetime.combine(next_bus.date(), source_hour)
                    ).total_seconds()
                    minutes, seconds = divmod(time_until_next_bus, 60)
                    time_until_next_buses.append((int(minutes), int(seconds)))

                return time_until_next_buses

    def timedelta_to_hhmm(self, td):
        """
        Converts a timedelta object to a string in HHMM format.

        Parameters:
        td (timedelta): The timedelta object to be converted.

        Returns:
        str: A formated string with the time.
        """
        total_seconds = int(td.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f"{hours:02d}:{minutes:02d}"

    def timedelta_separator(self, td):
        """
        Separates a timedelta object into minutes and seconds.

        Parameters:
        td (timedelta): A timedelta object representing a duration of time.

        Returns:
        tuple: A tuple containing the number of minutes and seconds in the timedelta object. The minutes and seconds are both integers.
        """
        total_seconds = td.total_seconds()
        minutes = int(total_seconds // 60)
        seconds = int(total_seconds % 60)
        return minutes, seconds

    def get_travel_time(self, trip_id, stop_ids):
        """
        Returns the travel time between two stops for a given trip.

        Parameters:
        trip_id (str): A string representing the ID of the trip.
        stop_ids (list): A list of two strings representing the IDs of the stops.

        Returns:
        timedelta: A timedelta object representing the travel time.
        """
        stop_times = pd.read_csv("stop_times.txt").query(
            f"trip_id.str.startswith('{trip_id}') and stop_id in {stop_ids}"
        )
        if len(stop_times) < 2:
            return None
        arrival_times = [
            datetime.strptime(arrival_time, "%H:%M:%S")
            for arrival_time in stop_times["arrival_time"]
        ]
        travel_time = arrival_times[1] - arrival_times[0]
        return travel_time

    def get_trip_sequence(self, route_id, stop_id):
        """
        Given a dictionary of routes and stops, a route ID and a stop ID, gets the trip sequence number corresponding to the stop.

        Parameters:
        route_id (str): The route or service's ID.
        stop_id (str): The stop's ID.

        Returns:
        str: A string representing the sequence number.
        """
        seq = self.route_stops[route_id][stop_id]["sequence"]
        return seq

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

    def walking_travel_time(self, stop_coords, location_coords, speed):
        """
        Calculates the walking travel time between a location and a stop, given a speed value.

        Parameters:
        stop_coords (tuple): A tuple with the stop's coordinates (lat, lon).
        location_coords (tuple):  A tuple with the location's coordinates (lat, lon).
        speed (float): The walking speed value in km/h.

        Returns.
        float: The time (in seconds) that represents the travel time.
        """
        # Extract lat/lon from tuples (order: lat, lon)
        stop_lat, stop_lon = stop_coords
        location_lat, location_lon = location_coords

        # Call haversine with correct parameter order: lon1, lat1, lon2, lat2
        distance = self.haversine(stop_lon, stop_lat, location_lon, location_lat)

        time = round((distance / speed) * 3600, 2)
        return time

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
        coords = self._stop_coords.get(stop_id)
        if coords is not None:
            return coords

        # Fallback: parada presente en el scheduler pero no en ninguna ruta
        try:
            stop = self.scheduler.stops_by_id(stop_id)
            if stop and len(stop) > 0:
                stop_obj = stop[0]
                if stop_obj.stop_lon is not None and stop_obj.stop_lat is not None:
                    resolved = (stop_obj.stop_lon, stop_obj.stop_lat)
                    self._stop_coords[stop_id] = resolved
                    return resolved
        except (KeyError, AttributeError, ValueError) as e:
            logging.getLogger(__name__).warning(
                "get_stop_coords fallback failed for %s: %s",
                stop_id,
                e,
            )

        return None

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

    def get_transfer_options(self, route_id: str, stop_id: str, viable_only: bool = True):
        """
        Obtiene opciones de transbordo desde una parada de una ruta.

        Args:
            route_id: ID de la ruta actual
            stop_id: ID de la parada actual
            viable_only: Si True, solo retorna transferencias viables

        Returns:
            list: Lista de TransferConnection disponibles
        """
        if not hasattr(self, "transfer_manager"):
            return []

        if viable_only:
            return self.transfer_manager.get_viable_transfers_from(route_id, stop_id)
        else:
            return self.transfer_manager.get_transfers_from(route_id, stop_id)

    def parse_metro_stations(self, stops_file):
        """
        Parses the Metro Stations data, creating a dictionary with their names.

        Parameters:
        stops_file (File): The GTFS file with the stop data (stops.txt).

        Returns:
        dict: A dictionary with the names of the stations.
        """
        subway_stops = {}
        with open(stops_file) as f:
            for line in f:
                stop_id, _, stop_name, _, _, _, _ = line.strip().split(",")
                if stop_id.isdigit():
                    subway_stops[stop_id] = stop_name
        return subway_stops

    def is_metro_station(self, stop_id, route_dict):
        """
        Checks if a stop is a Metro station.

        Parameters:
        stop_id (str): The stop's ID to be checked.
        route_dict (dict): The dictionary with the Metro stations names.

        Returns:
        str or None: A string with the stop ID if the stop is a Metro station, or None if it isn't.
        """
        try:
            route_num = int(stop_id)
            return route_dict[stop_id]
        except ValueError:
            return None
