"""Regresión: el Metro debe seguir apareciendo en los viajes.

Causa histórica del bug "el metro nunca aparece": el feed se actualizó (2023 →
2026) cambiando los stop_ids del Metro, pero el cache de transbordos
(``transfers.json``) quedó con los ids viejos. ``_validate_transfer_cache`` no
lo detectaba porque su umbral global del 10% lo dominan los ~11k paraderos de
bus (estables entre versiones), así que el cache obsoleto se servía y TODOS los
footpaths bus↔metro quedaban huérfanos (su ``to_stop`` ya no existía).

El fix endurece la validación con un chequeo **por modo**: si casi todas las
paradas de un modo (p.ej. metro) desaparecieron del feed, recomputa el cache.

- ``TestTransferCacheModeValidation``: unit test rápido del chequeo por modo
  (no carga ningún feed). Es la guardia directa del fix.
- ``TestMetroAppearsInJourney``: integración end-to-end (carga el feed 2026 +
  cache; ~varios minutos). Gated tras ``AYATORI_RUN_SLOW_TESTS=1`` para no
  ralentizar ``pytest`` por defecto.
"""

import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from ayatori.models.GTFSData import GTFSData
from ayatori.models.TransferConnection import TransferConnection, TransferManager


class _FakeFeed:
    """``self`` mínimo para ejercitar ``GTFSData._validate_transfer_cache`` sin
    cargar un feed real: solo necesita ``stops`` y ``get_route_mode``."""

    def __init__(self, stops, route_modes):
        self.stops = set(stops)
        self._route_modes = route_modes

    def get_route_mode(self, route_id):
        return self._route_modes.get(route_id, "bus")


def _tm_from(pairs):
    """Construye un TransferManager desde tuplas (from_route, to_route, from_stop, to_stop)."""
    tm = TransferManager()
    for fr, tr, fs, ts in pairs:
        tm.add_transfer(TransferConnection(fr, tr, fs, ts, 0.1, 72.0))
    return tm


class TestTransferCacheModeValidation(unittest.TestCase):
    """Guardia del fix: el chequeo por modo de ``_validate_transfer_cache``."""

    def test_metro_id_change_forces_recompute(self):
        # Feed nuevo: buses estables + estaciones de metro con ids NUEVOS.
        feed_stops = {f"PB{i}" for i in range(200)} | {f"M_NEW{i}" for i in range(30)}
        modes = {"101": "bus", "B14": "bus", "L1": "metro"}
        # Cache viejo: bus->bus válidos + bus->metro cuyo to_stop ya NO existe.
        pairs = [("101", "B14", f"PB{i}", f"PB{(i + 1) % 200}") for i in range(200)]
        pairs += [("101", "L1", f"PB{i}", f"M_OLD{i}") for i in range(30)]  # >20 metro stops
        tm = _tm_from(pairs)
        fake = _FakeFeed(feed_stops, modes)
        # El global pasa (from_stops son buses válidos) pero el modo metro no.
        with self.assertRaises(ValueError):
            GTFSData._validate_transfer_cache(fake, tm)

    def test_consistent_cache_passes(self):
        feed_stops = {f"PB{i}" for i in range(200)} | {f"M_NEW{i}" for i in range(30)}
        modes = {"101": "bus", "B14": "bus", "L1": "metro"}
        pairs = [("101", "B14", f"PB{i}", f"PB{(i + 1) % 200}") for i in range(200)]
        pairs += [("101", "L1", f"PB{i}", f"M_NEW{i}") for i in range(30)]  # metro presente
        tm = _tm_from(pairs)
        fake = _FakeFeed(feed_stops, modes)
        try:
            GTFSData._validate_transfer_cache(fake, tm)
        except ValueError as e:  # no debe lanzar
            self.fail(f"validación falló con un cache consistente: {e}")

    def test_global_threshold_still_triggers(self):
        # >10% de from_stops desconocidos -> recompute (chequeo global preexistente).
        feed_stops = {f"PB{i}" for i in range(100)}
        modes = {"101": "bus", "B14": "bus"}
        pairs = [("101", "B14", f"GONE{i}", "PB0") for i in range(50)]
        pairs += [("101", "B14", f"PB{i}", "PB0") for i in range(100)]
        tm = _tm_from(pairs)
        fake = _FakeFeed(feed_stops, modes)
        with self.assertRaises(ValueError):
            GTFSData._validate_transfer_cache(fake, tm)


class TestRailAccessSeeding(unittest.TestCase):
    """Guardia del fix "riel desplazado del top-N por densidad de bus".

    En una estación intermodal (p.ej. Los Libertadores) los paraderos de bus
    rodean la plataforma de metro y la empujan fuera del top-N de acceso, así
    que el motor solo podía "entrar" a la estación con un bus-hop + footpath
    espurios. ``_candidate_stops`` (sin filtros de modo) debe inyectar la
    estación de riel cercana igual. No carga feed real.
    """

    class _StubGTFS:
        _stop_to_routes: dict = {}

        def __init__(self):
            # 12 buses < 100 m + 2 plataformas de metro a 124 m (rank 12/13),
            # ordenadas por distancia como devuelve get_nearby_stops.
            self._all = [(f"PB{i}", 0.010 + i * 0.008) for i in range(12)] + [
                ("LIB_L3_V1", 0.124),
                ("LIB_L3_V2", 0.124),
            ]
            self.stop_modes = {sid: {"bus"} for sid, _ in self._all[:12]}
            self.stop_modes["LIB_L3_V1"] = {"metro"}
            self.stop_modes["LIB_L3_V2"] = {"metro"}

        def get_nearby_stops(self, coords, margin_km=0.5, max_stops=10):
            return [(s, d) for s, d in self._all if d <= margin_km][:max_stops]

    def _csa(self, gtfs, **cfg):
        from ayatori.models.ConnectionScanAlgorithm import (
            CSAConfig,
            ConnectionScanAlgorithm,
        )

        return ConnectionScanAlgorithm(gtfs, config=CSAConfig(**cfg))

    def test_rail_injected_when_topN_all_bus(self):
        csa = self._csa(self._StubGTFS(), max_origin_stops=8)
        ids = [sid for sid, _ in csa._candidate_stops((0.0, 0.0), 1.0, 8)]
        self.assertIn("LIB_L3_V1", ids, ids)
        self.assertIn("LIB_L3_V2", ids, ids)
        # No se amplió la densidad de bus: siguen siendo los 8 del top-N.
        self.assertEqual(sum(s.startswith("PB") for s in ids), 8, ids)

    def test_no_injection_when_rail_already_present(self):
        class _WithRail(self._StubGTFS):
            def __init__(self):
                super().__init__()
                self._all = [("MET_V1", 0.05), ("MET_V2", 0.06)] + self._all
                self.stop_modes["MET_V1"] = {"metro"}
                self.stop_modes["MET_V2"] = {"metro"}

        csa = self._csa(_WithRail(), max_origin_stops=8)
        ids = [sid for sid, _ in csa._candidate_stops((0.0, 0.0), 1.0, 8)]
        self.assertEqual(len(ids), 8, ids)  # sin inyección extra


_SLOW = os.environ.get("AYATORI_RUN_SLOW_TESTS") == "1"
_FEED_2026 = "ayatori/data/GTFS/GTFS_20260425_v3.zip"
_CACHE = "ayatori/data/cache/transfers.json"


@unittest.skipUnless(
    _SLOW and os.path.exists(_FEED_2026) and os.path.exists(_CACHE),
    "integración lenta: requiere AYATORI_RUN_SLOW_TESTS=1 y el feed 2026 + cache",
)
class TestMetroAppearsInJourney(unittest.TestCase):
    """End-to-end con el feed canónico (2026): el metro aparece en viajes reales."""

    @classmethod
    def setUpClass(cls):
        from ayatori.models.ConnectionScanAlgorithm import (
            CSAConfig,
            ConnectionScanAlgorithm,
        )

        cls.gtfs = GTFSData(_FEED_2026)
        cls.tm = cls.gtfs.get_or_compute_transfers(cache_path=_CACHE, max_distance_km=0.5)
        cls.csa = ConnectionScanAlgorithm(
            cls.gtfs, transfer_manager=cls.tm, config=CSAConfig()
        )

    @staticmethod
    def _transit_modes(journey):
        return {s.get("mode") for s in journey.segments if s.get("type") == "transit"}

    def test_metro_in_crosstown_journey(self):
        # San Pablo (poniente) -> Los Dominicos (oriente): corredor de la L1.
        journeys = self.csa.find_journey(
            (-33.444218, -70.723302),
            (-33.407895, -70.545109),
            datetime(2026, 6, 9, 8, 0),
            num_alternatives=5,
            profile="balanced",
        )
        self.assertTrue(journeys, "no se encontró ningún itinerario")
        self.assertTrue(
            any("metro" in self._transit_modes(j) for j in journeys),
            "ningún itinerario usa metro en un OD claramente favorable al metro",
        )

    def test_intermodal_origin_boards_rail_directly(self):
        # Regresión Los Libertadores→JGM: el origen ES la estación de metro
        # (terminal L3), pero rodeada de paraderos de bus. Antes el mejor viaje
        # caminaba a un bus, viajaba UN paradero dentro de la estación y recién
        # ahí transbordaba al metro (transbordo espurio). Ahora debe abordar el
        # metro directo: primer tramo transit = metro, y ≤1 transbordo.
        journeys = self.csa.find_journey(
            (-33.36543, -70.69199),
            (-33.46750, -70.59583),
            datetime(2026, 6, 9, 8, 0),
            num_alternatives=5,
            profile="balanced",
        )
        self.assertTrue(journeys, "no se encontró itinerario Los Libertadores→JGM")
        best = journeys[0]
        first_transit = next(
            (s for s in best.segments if s.get("type") == "transit"), None
        )
        self.assertIsNotNone(first_transit, "el mejor viaje no tiene tramo de tránsito")
        self.assertEqual(
            first_transit.get("mode"),
            "metro",
            "el mejor viaje no aborda el metro directamente en Los Libertadores",
        )
        self.assertLessEqual(
            best.number_of_transfers, 1, "transbordo espurio al entrar a la estación"
        )

    def test_short_bus_trip_still_returns_transit(self):
        # Plaza de Armas -> Estación Central: no debe romperse el caso de bus.
        journeys = self.csa.find_journey(
            (-33.4489, -70.6693),
            (-33.4380, -70.6289),
            datetime(2026, 6, 9, 8, 0),
            num_alternatives=3,
            profile="balanced",
        )
        self.assertTrue(journeys, "no se encontró itinerario para el viaje corto de bus")


if __name__ == "__main__":
    unittest.main(verbosity=2)
