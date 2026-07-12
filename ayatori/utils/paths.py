from pathlib import Path

from pyprojroot import here


def data_dir(*args) -> Path:
    """Path al directorio ``data/`` del proyecto (opcionalmente con subrutas)."""
    return here().joinpath("data", *args)
