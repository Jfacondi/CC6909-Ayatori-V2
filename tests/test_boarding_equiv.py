"""Regresión: el _next_boarding integerizado elige EXACTAMENTE el mismo abordaje
que la versión histórica basada en datetime.time.

La optimización reemplazó la materialización de un datetime.time por boarding (12M+
objetos por consulta) por segundos-del-día enteros. Es un cambio de rendimiento que
NO debe alterar resultados. Este test ejercita el método real (_next_boarding) y lo
compara contra una copia inline de la lógica antigua sobre un barrido de horas que
incluye: empates de tod (mismo segundo, distinto trip/dispatch), after_time con
microsegundos (los introduce el tiempo de caminata fraccionario), y un despacho que
cruza medianoche (tod envuelto por el módulo). No carga ningún feed.
"""
import os
import sys
import types
import unittest
from bisect import bisect_left
from datetime import datetime, time, timedelta

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ayatori.models.ConnectionScanAlgorithm import ConnectionScanAlgorithm

_SECS_DAY = 24 * 3600


def _old_next_boarding(stop_info, trip_service, trip_dispatches, after_time, active_services):
    """Copia fiel de la implementación previa (datetime.time + sort + bisect)."""
    trips_here = stop_info.get("trips_here") or []

    def expand(filter_service):
        out = []
        for trip_id, offset in trips_here:
            if filter_service:
                sid = trip_service.get(trip_id)
                if sid is None or sid not in active_services:
                    continue
            disp = trip_dispatches.get(trip_id)
            if not disp:
                continue
            for d in disp:
                abs_secs = d + offset
                tod = abs_secs % _SECS_DAY
                t = time(tod // 3600, (tod // 60) % 60, tod % 60)
                out.append((t, trip_id, d))
        out.sort(key=lambda x: x[0])
        return out

    boardings = []
    if active_services:
        boardings = expand(True)
    if not boardings:
        boardings = expand(False)
    if not boardings:
        return None
    ato = after_time.time()
    idx = bisect_left(boardings, ato, key=lambda b: b[0])
    if idx >= len(boardings):
        return None
    bt, trip_id, disp = boardings[idx]
    bdt = datetime.combine(after_time.date(), bt)
    if bdt < after_time:
        return None
    return bdt, (trip_id, disp)


def _make_csa(route_id, stop_id, trips_here, trip_service, trip_dispatches):
    """CSA mínimo: solo enlaza self.gtfs con lo que _next_boarding consulta."""
    gtfs = types.SimpleNamespace(
        route_stops={route_id: {stop_id: {"trips_here": trips_here}}},
        trip_service=trip_service,
        trip_dispatches=trip_dispatches,
    )
    csa = ConnectionScanAlgorithm.__new__(ConnectionScanAlgorithm)
    csa.gtfs = gtfs
    return csa


class TestBoardingEquivalence(unittest.TestCase):
    def setUp(self):
        # tA y tB empatan en tod=28800 (08:00:00): A offset 0 disp 28800;
        # B offset 100 disp 28700. trips_here pone A antes que B → A debe ganar.
        # tA también despacha por frecuencia cada 600s. tC cruza medianoche
        # (disp 90000 = 25:00 → tod 3600). tD opera con servicio S2 (inactivo).
        self.route, self.stop = "R1", "S1"
        self.trips_here = [("tA", 0), ("tB", 100), ("tC", 0), ("tD", 0)]
        self.trip_service = {"tA": "S1", "tB": "S1", "tC": "S1", "tD": "S2"}
        self.trip_dispatches = {
            "tA": list(range(28800, 36000, 600)),  # 08:00..09:50 cada 10 min
            "tB": [28700, 31700],                   # 08:00:00 (28700+100), 08:51:40
            "tC": [90000],                          # 25:00 → tod 01:00
            "tD": [27000],                          # 07:30 pero servicio inactivo
        }
        self.active = frozenset({"S1"})

    def _sweep_after_times(self):
        base = datetime(2026, 6, 9)
        out = []
        # cada minuto del día (entero) + variantes con microsegundos en horas clave
        for secs in range(0, _SECS_DAY, 60):
            out.append(base + timedelta(seconds=secs))
        for whole in (28799, 28800, 28801, 31699, 31700, 3599, 3600, 3601):
            for micro in (0, 1, 500_000, 999_999):
                out.append(base + timedelta(seconds=whole, microseconds=micro))
        return out

    def _check(self, active):
        csa = _make_csa(
            self.route, self.stop, self.trips_here, self.trip_service, self.trip_dispatches
        )
        cache = {}
        for after in self._sweep_after_times():
            new = csa._next_boarding(self.route, self.stop, after, active, cache)
            old = _old_next_boarding(
                csa.gtfs.route_stops[self.route][self.stop],
                self.trip_service,
                self.trip_dispatches,
                after,
                active,
            )
            self.assertEqual(new, old, f"divergencia en after={after.isoformat()} active={active}")

    def test_equivalence_with_active_services(self):
        self._check(self.active)

    def test_equivalence_without_calendar(self):
        # Sin filtro de servicio: tD (S2) entra al pool; el orden/empate debe coincidir.
        self._check(frozenset())

    def test_tie_breaks_to_first_in_trips_here_order(self):
        # Garantía explícita del empate 08:00:00 → gana tA (aparece antes que tB).
        csa = _make_csa(
            self.route, self.stop, self.trips_here, self.trip_service, self.trip_dispatches
        )
        res = csa._next_boarding(
            self.route, self.stop, datetime(2026, 6, 9, 7, 59), self.active, {}
        )
        self.assertIsNotNone(res)
        _, (trip_id, _disp) = res
        self.assertEqual(trip_id, "tA")


if __name__ == "__main__":
    unittest.main(verbosity=2)
