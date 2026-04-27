"""Models package for Ayatori."""

from .GTFSData import GTFSData
from .TransferConnection import TransferConnection, TransferManager
from .JourneyPlanner import JourneyPlanner, Journey, JourneyLeg, create_journey_planner
from .JourneyPlannerV2 import JourneyPlannerV2, create_journey_planner_v2
from .ConnectionScanAlgorithm import ConnectionScanAlgorithm, create_csa_planner
from .MultimodalLayer import MultimodalLayer

try:
    from .OSMGraph import OSMGraph
except ModuleNotFoundError:
    OSMGraph = None

__all__ = [
    'GTFSData',
    'TransferConnection',
    'TransferManager',
    'JourneyPlanner',
    'Journey',
    'JourneyLeg',
    'create_journey_planner',
    'JourneyPlannerV2',
    'create_journey_planner_v2',
    'ConnectionScanAlgorithm',
    'create_csa_planner',
    'MultimodalLayer',
]

if OSMGraph is not None:
    __all__.append('OSMGraph')
