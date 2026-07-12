"""Unit tests de ``GTFSData.common_lines_for_segment`` (líneas comunes).

Patrón "líneas comunes" (common lines): cuando varias líneas cubren el mismo
tramo —misma subida, misma bajada, corredor compatible— son intercambiables y
se presentan como opciones de recorrido del mismo viaje.

Estos tests NO cargan un feed real: arman un ``self`` mínimo (stub) con las
estructuras que el método consulta y le enlazan los métodos reales de
``GTFSData`` (mismo enfoque que ``_FakeFeed`` en ``test_metro_present.py``), de
modo que ejercitan la implementación verdadera de forma rápida y determinística.
"""

import os
import sys
import types
import unittest
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ayatori.models.GTFSData import GTFSData


def _make_stub(routes, *, active=frozenset(), schedules=None):
    """Construye un ``self`` falso para ``common_lines_for_segment``.

    Args:
        routes: ``{route_id: [stop_id, ...]}`` con las paradas en orden (sentido
            0). Define ``_stop_to_routes``, ``_route_dir_stop_order``,
            ``_route_dir_stops_ordered``, ``route_stops`` y ``route_meta``.
        active: frozenset de service_ids activos que devuelve ``active_services_on``.
        schedules: ``{route_id: {stop_id: [(trip_id, offset_secs, service_id,
            [dispatch_secs, ...]), ...]}}`` para poblar el filtro de horario.
    """
    stub = types.SimpleNamespace()
    stub._stop_to_routes = defaultdict(list)
    stub.route_stops = {}
    stub._route_dir_stop_order = {}
    stub._route_dir_stops_ordered = {}
    stub.route_meta = {}
    stub.trip_service = {}
    stub.trip_dispatches = {}

    for rid, stops in routes.items():
        stub.route_stops[rid] = {
            sid: {"sequence": i, "trips_here": []} for i, sid in enumerate(stops)
        }
        for sid in stops:
            stub._stop_to_routes[sid].append(rid)
        stub._route_dir_stop_order[(rid, 0)] = {sid: i for i, sid in enumerate(stops)}
        stub._route_dir_stops_ordered[(rid, 0)] = list(stops)
        stub.route_meta[rid] = {
            "short_name": rid,
            "long_name": f"{rid} larga",
            "route_color": None,
            "mode": "bus",
        }

    if schedules:
        for rid, by_stop in schedules.items():
            for sid, entries in by_stop.items():
                th = stub.route_stops[rid][sid]["trips_here"]
                for trip_id, offset, service_id, dispatches in entries:
                    th.append((trip_id, offset))
                    stub.trip_service[trip_id] = service_id
                    stub.trip_dispatches[trip_id] = list(dispatches)

    stub.connection_finder = types.MethodType(GTFSData.connection_finder, stub)
    stub._next_departure_secs = types.MethodType(GTFSData._next_departure_secs, stub)
    stub.common_lines_for_segment = types.MethodType(
        GTFSData.common_lines_for_segment, stub
    )
    stub.active_services_on = lambda _d: active
    return stub


def _hms(h, m=0, s=0):
    return h * 3600 + m * 60 + s


class TestCommonLinesCorridor(unittest.TestCase):
    """Matching de corredor (sin filtro de hora: ``departure_dt=None``)."""

    def setUp(self):
        # LOCAL recorre A-B-C-D (tramo elegido). EXPRESS se salta B (variante
        # válida). OTHER toma otra calle (X,Y) entre A y D. WRONG va al revés.
        # PARTIAL no llega a D. SELF es la propia línea elegida.
        self.routes = {
            "LOCAL": ["A", "B", "C", "D"],
            "EXPRESS": ["A", "C", "D"],
            "OTHER": ["A", "X", "Y", "D"],
            "WRONG": ["D", "C", "B", "A"],
            "PARTIAL": ["A", "B", "C"],
        }

    def _opts(self):
        stub = _make_stub(self.routes)
        res = stub.common_lines_for_segment("LOCAL", ["A", "B", "C", "D"], None)
        return res, {o["route_id"] for o in res}

    def test_express_variant_is_common_line(self):
        _res, ids = self._opts()
        self.assertIn("EXPRESS", ids, "un expreso que salta paraderos debe agruparse")

    def test_other_street_excluded(self):
        _res, ids = self._opts()
        self.assertNotIn("OTHER", ids, "una línea por otra calle no es el mismo tramo")

    def test_reverse_direction_excluded(self):
        _res, ids = self._opts()
        self.assertNotIn("WRONG", ids, "el sentido contrario no sirve el tramo")

    def test_partial_excluded(self):
        _res, ids = self._opts()
        self.assertNotIn("PARTIAL", ids, "una línea que no llega a la bajada no aplica")

    def test_self_excluded(self):
        _res, ids = self._opts()
        self.assertNotIn("LOCAL", ids, "la línea elegida no se lista como alternativa")

    def test_only_express_returned(self):
        _res, ids = self._opts()
        self.assertEqual(ids, {"EXPRESS"})

    def test_overlap_threshold_rejects_low_overlap(self):
        # Tramo largo; candidata que comparte sólo 1 de 4 intermedios + 2 ajenos.
        routes = {
            "LOCAL": ["A", "B", "C", "D", "E", "F"],
            "DIVERGE": ["A", "B", "P", "Q", "F"],  # comparte B; agrega P,Q
        }
        stub = _make_stub(routes)
        res = stub.common_lines_for_segment(
            "LOCAL", ["A", "B", "C", "D", "E", "F"], None
        )
        # inter_leg={B,C,D,E}, inter_cand={B,P,Q}; solape=1/3 < 0.5 → excluida.
        self.assertEqual(res, [])


class TestCommonLinesSchedule(unittest.TestCase):
    """Filtro por servicio activo y próxima salida en ventana."""

    def test_filters_by_window_and_calendar(self):
        routes = {
            "LOCAL": ["A", "B", "C", "D"],
            "SOON": ["A", "C", "D"],   # pasa pronto (dentro de ventana)
            "LATE": ["A", "B", "C", "D"],  # corredor ok pero pasa muy tarde
        }
        schedules = {
            # subida en A; (trip_id, offset, service_id, [dispatch_secs])
            "SOON": {"A": [("t_soon", 0, "S1", [_hms(8, 5)])]},
            "LATE": {"A": [("t_late", 0, "S1", [_hms(9, 0)])]},
        }
        stub = _make_stub(routes, active=frozenset({"S1"}), schedules=schedules)
        dep = datetime(2026, 6, 9, 8, 0, 0)  # salida 08:00, ventana +15 min
        res = stub.common_lines_for_segment("LOCAL", ["A", "B", "C", "D"], dep)
        ids = {o["route_id"] for o in res}
        self.assertEqual(ids, {"SOON"}, "sólo la línea con paso próximo debe quedar")
        soon = next(o for o in res if o["route_id"] == "SOON")
        self.assertEqual(soon["next_departure_secs"], _hms(8, 5))

    def test_inactive_service_excluded(self):
        routes = {"LOCAL": ["A", "B", "C", "D"], "OTHERDAY": ["A", "C", "D"]}
        schedules = {"OTHERDAY": {"A": [("t_o", 0, "S2", [_hms(8, 5)])]}}
        # Sólo S1 activo hoy; OTHERDAY corre con S2 → no opera hoy.
        stub = _make_stub(routes, active=frozenset({"S1"}), schedules=schedules)
        dep = datetime(2026, 6, 9, 8, 0, 0)
        res = stub.common_lines_for_segment("LOCAL", ["A", "B", "C", "D"], dep)
        self.assertEqual(res, [], "una línea que no opera ese día no es alternativa")


if __name__ == "__main__":
    unittest.main(verbosity=2)
