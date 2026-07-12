"""Tests de la capa API para líneas comunes (enriquecimiento + colapso).

Se saltan si FastAPI/pydantic no están instalados (la API corre vía Docker o
con el extra ``api``), igual que ``test_basic`` se salta si falta ``pyrosm``.
Ejercitan el código real:

- ``api.schemas._common_lines_by_index``: agrupa los hops transit de un mismo
  tramo y adjunta ``route_options`` (las líneas que cubren ese tramo) a cada hop.
- ``api.main._collapse_equivalent_journeys``: colapsa viajes con idéntica firma
  de paraderos (mismo trayecto, distinta línea) conservando el más temprano.
"""

import os
import sys
import types
from collections import defaultdict
from datetime import datetime, timedelta

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ayatori.models.GTFSData import GTFSData


def _make_gtfs(routes):
    """``gtfs`` mínimo cuyo ``common_lines_for_segment`` es el real de GTFSData."""
    g = types.SimpleNamespace()
    g._stop_to_routes = defaultdict(list)
    g.route_stops = {}
    g._route_dir_stop_order = {}
    g._route_dir_stops_ordered = {}
    g.route_meta = {}
    g.trip_service = {}
    g.trip_dispatches = {}
    for rid, stops in routes.items():
        g.route_stops[rid] = {s: {"sequence": i, "trips_here": []} for i, s in enumerate(stops)}
        for s in stops:
            g._stop_to_routes[s].append(rid)
        g._route_dir_stop_order[(rid, 0)] = {s: i for i, s in enumerate(stops)}
        g._route_dir_stops_ordered[(rid, 0)] = list(stops)
        g.route_meta[rid] = {"short_name": rid, "long_name": rid, "route_color": None, "mode": "bus"}
    g.connection_finder = types.MethodType(GTFSData.connection_finder, g)
    g._next_departure_secs = types.MethodType(GTFSData._next_departure_secs, g)
    g.common_lines_for_segment = types.MethodType(GTFSData.common_lines_for_segment, g)
    g.active_services_on = lambda _d: frozenset()
    return g


def _hop(route_id, a, b):
    return {
        "type": "transit",
        "route_id": route_id,
        "from_stop": a,
        "to_stop": b,
        "departure_time": None,
    }


def test_leg_grouping_attaches_route_options():
    pytest.importorskip("pydantic")
    from api import schemas

    gtfs = _make_gtfs(
        {"LOCAL": ["A", "B", "C", "D"], "EXPRESS": ["A", "C", "D"], "OTHER": ["A", "X", "Y", "D"]}
    )
    segments = [
        {"type": "walk", "from": "origin", "to": "A"},
        _hop("LOCAL", "A", "B"),
        _hop("LOCAL", "B", "C"),
        _hop("LOCAL", "C", "D"),
        {"type": "walk", "from": "D", "to": "destination"},
    ]
    idx = schemas._common_lines_by_index(segments, gtfs)

    # Los 3 hops del tramo reciben opciones; los walks (0, 4) no.
    assert sorted(idx.keys()) == [1, 2, 3]
    # EXPRESS es la única línea común (OTHER va por otra calle).
    assert {o["route_id"] for o in idx[1]} == {"EXPRESS"}
    # La misma lista se comparte en todo el tramo (la consolidación la conserva).
    assert idx[1] is idx[2] is idx[3]


def test_collapse_equivalent_journeys_keeps_earliest():
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    from api import main

    base = datetime(2026, 6, 9, 8, 0, 0)

    class _J:
        def __init__(self, segs, arr):
            self.segments = segs
            self.arrival_time = arr

    # jA y jB: mismo trayecto por paraderos (A→D), distinta línea → colapsan.
    j_a = _J([_hop("LOCAL", "A", "D")], base + timedelta(minutes=30))
    j_b = _J([_hop("OTRA", "A", "D")], base + timedelta(minutes=27))
    # jC: otro destino (A→E) → trayecto distinto, se conserva aparte.
    j_c = _J([_hop("X", "A", "E")], base + timedelta(minutes=25))

    out = main._collapse_equivalent_journeys([j_a, j_b, j_c])
    assert len(out) == 2
    # Conserva el de llegada más temprana entre los equivalentes (jB: 27 < 30).
    assert out[0] is j_b
    assert out[1] is j_c


if __name__ == "__main__":
    import unittest

    unittest.main(verbosity=2)
