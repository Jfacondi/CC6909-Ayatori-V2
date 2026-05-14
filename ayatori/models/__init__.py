"""Models package for Ayatori."""

from .ConnectionScanAlgorithm import ConnectionScanAlgorithm, create_csa_planner
from .GTFSData import GTFSData

# DEPRECATED — se removerá en 0.3.0. Mantener para retro-compatibilidad
# de scripts existentes (demo_funcional.py, examples/journey_planner_usage.py).
from .JourneyPlanner import (  # noqa: F401
    Journey,
    JourneyLeg,
    JourneyPlanner,
    create_journey_planner,
)
from .JourneyPlannerV2 import JourneyPlannerV2, create_journey_planner_v2
from .TransferConnection import TransferConnection, TransferManager

try:
    from .OSMGraph import OSMGraph
except ModuleNotFoundError:
    OSMGraph = None

__all__ = [
    "GTFSData",
    "TransferConnection",
    "TransferManager",
    "JourneyPlannerV2",
    "create_journey_planner_v2",
    "ConnectionScanAlgorithm",
    "create_csa_planner",
    # Deprecated:
    "JourneyPlanner",
    "Journey",
    "JourneyLeg",
    "create_journey_planner",
]

if OSMGraph is not None:
    __all__.append("OSMGraph")
