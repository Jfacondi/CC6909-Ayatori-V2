"""
GTFS Cleaner Module
Handles cleaning and validation of GTFS data with missing or invalid coordinates.
"""

import zipfile
import csv
import tempfile
import shutil
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


def clean_gtfs_stops(gtfs_zip_path):
    """
    Creates a cleaned copy of GTFS by removing stops without valid coordinates.

    This function extracts the GTFS ZIP, validates stops in stops.txt,
    and creates a new ZIP with only valid stops.

    Parameters:
        gtfs_zip_path (str or Path): Path to the original GTFS.zip file

    Returns:
        Path: Path to the cleaned GTFS.zip file (temporary location)

    Raises:
        FileNotFoundError: If gtfs_zip_path doesn't exist
        zipfile.BadZipFile: If the file is not a valid ZIP
    """
    gtfs_path = Path(gtfs_zip_path)

    if not gtfs_path.exists():
        raise FileNotFoundError(f"GTFS file not found: {gtfs_path}")

    # Create temporary directory
    temp_dir = Path(tempfile.mkdtemp())
    cleaned_zip = temp_dir / "gtfs_cleaned.zip"
    extract_dir = temp_dir / "gtfs_extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    try:
        # Extract original GTFS
        with zipfile.ZipFile(gtfs_path, "r") as zip_ref:
            zip_ref.extractall(extract_dir)

        # Clean stops.txt
        stops_file = extract_dir / "stops.txt"
        if stops_file.exists():
            cleaned_rows = _clean_stops_file(stops_file)

            # Write cleaned stops.txt
            if cleaned_rows:
                with open(
                    stops_file, "w", encoding="utf-8", newline=""
                ) as f:
                    writer = csv.DictWriter(f, fieldnames=cleaned_rows[0].keys())
                    writer.writeheader()
                    writer.writerows(cleaned_rows)
                    logger.info(
                        f"Cleaned {len(cleaned_rows)} valid stops from GTFS"
                    )
            else:
                logger.warning("No valid stops found after cleaning!")

        # Create cleaned ZIP
        with zipfile.ZipFile(cleaned_zip, "w") as zf:
            for file in extract_dir.glob("**/*"):
                if file.is_file():
                    arcname = file.relative_to(extract_dir)
                    zf.write(file, arcname)

        logger.info(f"Created cleaned GTFS at: {cleaned_zip}")
        return cleaned_zip

    finally:
        # Clean up extraction directory (keep ZIP)
        if (extract_dir).exists():
            shutil.rmtree(extract_dir)


def _clean_stops_file(stops_file_path):
    """
    Reads stops.txt and returns only rows with valid coordinates.

    Parameters:
        stops_file_path (Path): Path to stops.txt file

    Returns:
        list: List of dictionaries representing valid stops
    """
    cleaned_rows = []

    with open(stops_file_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)

        for row_num, row in enumerate(reader, start=2):  # Start at 2 (after header)
            lat_str = row.get("stop_lat", "").strip()
            lon_str = row.get("stop_lon", "").strip()
            stop_id = row.get("stop_id", "unknown")

            # Validate coordinates exist
            if not lat_str or not lon_str:
                logger.debug(
                    f"Row {row_num}: Stop '{stop_id}' has missing coordinates"
                )
                continue

            # Validate coordinates are numeric
            try:
                lat = float(lat_str)
                lon = float(lon_str)
            except ValueError:
                logger.debug(
                    f"Row {row_num}: Stop '{stop_id}' has invalid coordinates: "
                    f"({lat_str}, {lon_str})"
                )
                continue

            # Validate coordinates are in valid range (WGS84)
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                logger.debug(
                    f"Row {row_num}: Stop '{stop_id}' has out-of-range coordinates: "
                    f"({lat}, {lon})"
                )
                continue

            # This stop is valid
            cleaned_rows.append(row)

    return cleaned_rows


def is_gtfs_valid(gtfs_zip_path):
    """
    Quick validation of GTFS file without full loading.

    Parameters:
        gtfs_zip_path (str or Path): Path to GTFS.zip

    Returns:
        bool: True if GTFS appears valid, False otherwise
    """
    gtfs_path = Path(gtfs_zip_path)

    if not gtfs_path.exists():
        return False

    try:
        with zipfile.ZipFile(gtfs_path, "r") as zf:
            # Check for required files
            required_files = [
                "stops.txt",
                "routes.txt",
                "trips.txt",
                "stop_times.txt",
            ]
            for req_file in required_files:
                if req_file not in zf.namelist():
                    logger.warning(f"Missing required file: {req_file}")
                    return False
        return True
    except zipfile.BadZipFile:
        logger.error(f"Invalid ZIP file: {gtfs_zip_path}")
        return False
