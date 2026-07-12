"""Models package for Ayatori."""

from .ConnectionScanAlgorithm import ConnectionScanAlgorithm
from .GTFSData import GTFSData
from .TransferConnection import TransferConnection, TransferManager

try:
    from .OSMGraph import OSMGraph
except ModuleNotFoundError:
    OSMGraph = None

__all__ = [
    "GTFSData",
    "TransferConnection",
    "TransferManager",
    "ConnectionScanAlgorithm",
]

if OSMGraph is not None:
    __all__.append("OSMGraph")
