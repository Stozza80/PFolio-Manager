"""Small parsing helpers shared by the broker parsers."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_EXCEL_EPOCH = datetime(1899, 12, 30)  # absorbs Excel's 1900 leap-year bug


def parse_it_number(text: str) -> float:
    """Parse an Italian-formatted number embedded in a label string,
    e.g. "Valore portafoglio : 67849,68€" -> 67849.68.
    """
    match = re.search(r"[-+]?\d{1,3}(?:\.\d{3})+(?:,\d+)?|[-+]?\d+(?:,\d+)?", text)
    if not match:
        raise ValueError(f"No numeric value found in: {text!r}")
    number = match.group(0).replace(".", "").replace(",", ".")
    return float(number)


def parse_directa_extraction_date(text: str) -> datetime:
    """Parse Directa's "Data estrazione : 2026/09/03 18:14:36" metadata line."""
    match = re.search(r"(\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})", text)
    if not match:
        raise ValueError(f"No extraction date found in: {text!r}")
    return datetime.strptime(match.group(1), "%Y/%m/%d %H:%M:%S")


def excel_serial_to_datetime(serial: float) -> datetime:
    """Convert an Excel date serial number (as used by BNL's "Situazione del") to a datetime."""
    return _EXCEL_EPOCH + timedelta(days=serial)


def parse_fx_cell(value) -> Optional[float]:
    """Parse a broker-supplied FX-rate cell, which may be a number, blank, or the text "-"."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if text in ("", "-"):
        return None
    try:
        return float(text.replace(",", "."))
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
