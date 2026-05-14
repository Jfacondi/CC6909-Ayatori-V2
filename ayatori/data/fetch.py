"""Descarga de datasets declarados en manifest.toml.

Uso:
    ayatori-fetch-data                       # descarga todos
    ayatori-fetch-data --only gtfs.2023-09-16
    ayatori-fetch-data --list                # lista datasets disponibles
    ayatori-fetch-data --force               # re-descarga aunque exista
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

logger = logging.getLogger("ayatori.fetch")

MANIFEST_PATH = Path(__file__).parent / "manifest.toml"


def _load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"Manifest no encontrado: {MANIFEST_PATH}")
    with MANIFEST_PATH.open("rb") as f:
        data = tomllib.load(f)
    return data.get("datasets", {})


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    logger.info("Descargando %s -> %s", url, dest)
    with urllib.request.urlopen(url) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.replace(dest)


def _extract_zip(zip_path: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    logger.info("Extrayendo %s en %s", zip_path.name, dest_dir)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest_dir)


def _fetch_one(name: str, spec: dict, root: Path, force: bool) -> None:
    url = spec["url"]
    dest = root / spec["dest"]
    expected_sha = spec.get("sha256", "") or ""
    extract = bool(spec.get("extract", False))

    if extract:
        if dest.exists() and any(dest.iterdir()) and not force:
            logger.info("[%s] ya existe en %s — skip", name, dest)
            return
        zip_dest = dest.with_suffix(".zip")
        _download(url, zip_dest)
        if expected_sha:
            got = _sha256(zip_dest)
            if got != expected_sha:
                zip_dest.unlink(missing_ok=True)
                raise RuntimeError(f"[{name}] sha256 mismatch (expected {expected_sha}, got {got})")
        _extract_zip(zip_dest, dest)
        zip_dest.unlink(missing_ok=True)
    else:
        if dest.exists() and not force:
            logger.info("[%s] ya existe en %s — skip", name, dest)
            return
        _download(url, dest)
        if expected_sha:
            got = _sha256(dest)
            if got != expected_sha:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"[{name}] sha256 mismatch (expected {expected_sha}, got {got})")

    logger.info("[%s] OK", name)


def _repo_root() -> Path:
    # ayatori/data/fetch.py -> repo root es 2 niveles arriba
    return Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Descarga datasets de Ayatori.")
    parser.add_argument(
        "--only", action="append", default=[], help="Nombre exacto del dataset (repetible)."
    )
    parser.add_argument("--list", action="store_true", help="Lista datasets y termina.")
    parser.add_argument("--force", action="store_true", help="Re-descarga aunque exista.")
    args = parser.parse_args(argv)

    datasets = _load_manifest()
    if args.list:
        for name, spec in datasets.items():
            print(f"{name:30s}  {spec['url']}")
        return 0

    targets = list(datasets.keys())
    if args.only:
        unknown = [n for n in args.only if n not in datasets]
        if unknown:
            logger.error("Datasets desconocidos: %s", unknown)
            return 2
        targets = args.only

    root = _repo_root()
    errors: list[str] = []
    for name in targets:
        try:
            _fetch_one(name, datasets[name], root, args.force)
        except Exception as e:
            logger.error("[%s] ERROR: %s", name, e)
            errors.append(name)
    if errors:
        logger.error("Fallaron: %s", errors)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
