from pathlib import Path

# Raíz del proyecto = dos niveles sobre ayatori/utils/paths.py.
# Antes se resolvía con pyprojroot.here() (dependencia extra, no declarada en
# pyproject/requirements, y relativa al CWD). Resolver desde __file__ es
# equivalente, sin dependencia y estable ante el directorio de trabajo.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def data_dir(*args) -> Path:
    """Path al directorio ``data/`` del proyecto (opcionalmente con subrutas)."""
    return _PROJECT_ROOT.joinpath("data", *args)
