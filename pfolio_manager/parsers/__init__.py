"""Broker report parsers.

Each submodule exposes `parse(file_path: Path) -> ParsedReport`. Broker detection
(`pfolio_manager.detect`) decides which one to call; parsers never guess at each other's formats.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .. import models


class ParserError(Exception):
    """Raised when a file doesn't match the expected structure for its broker."""


def find_header_row(rows: Sequence[Sequence], signature: set[str], search_rows: int = 15) -> int:
    """Return the 0-based index of the first row (within the first `search_rows`)
    whose non-empty string cells are a superset of `signature`.
    """
    for idx, row in enumerate(rows[:search_rows]):
        cell_texts = {str(c).strip() for c in row if isinstance(c, str) and str(c).strip()}
        if signature.issubset(cell_texts):
            return idx
    raise ParserError(f"Header row matching {signature!r} not found in first {search_rows} rows")


def header_name_to_col(header_row: Sequence) -> dict[str, int]:
    """Map header cell text -> 0-based column index, for the given header row."""
    mapping: dict[str, int] = {}
    for idx, cell in enumerate(header_row):
        if isinstance(cell, str) and cell.strip():
            mapping[cell.strip()] = idx
    return mapping


def to_float(value) -> float | None:
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


from .directa import parse as parse_directa  # noqa: E402
from .bnl import parse as parse_bnl  # noqa: E402
from .fineco import parse as parse_fineco  # noqa: E402

PARSERS = {
    "directa": parse_directa,
    "bnl": parse_bnl,
    "fineco": parse_fineco,
}
