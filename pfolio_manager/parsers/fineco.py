"""Parser for Fineco Bank "portafoglio-export" legacy .xls exports.

Fineco's export (unlike Directa/BNL) embeds neither an account/deposit identifier
nor a report date in the sheet content, so account_id uses a fixed placeholder
and as_of_date is recovered from the filename (falling back to file mtime).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import xlrd

from ..models import ParsedLine, ParsedReport
from . import ParserError, find_header_row, header_name_to_col, to_float

HEADER_SIGNATURE = {"Titolo", "ISIN", "Simbolo"}
TITLE_MARKER = "Portafoglio di sintesi"
DEFAULT_ACCOUNT_ID = "default"
_FILENAME_DATE_RE = re.compile(r"(\d{2})-(\d{2})-(\d{4})")


def _as_of_date_from_filename(file_path: Path) -> str:
    match = _FILENAME_DATE_RE.search(file_path.name)
    if match:
        day, month, year = match.groups()
        return date(int(year), int(month), int(day)).isoformat()
    return datetime.fromtimestamp(file_path.stat().st_mtime).date().isoformat()


def parse(file_path: Path) -> ParsedReport:
    wb = xlrd.open_workbook(file_path)
    sh = wb.sheet_by_index(0)
    rows = [sh.row_values(r) for r in range(sh.nrows)]

    if not rows or not any(TITLE_MARKER in str(cell) for cell in rows[0]):
        raise ParserError(f"Fineco file: expected title {TITLE_MARKER!r} not found in first row")

    header_idx = find_header_row(rows, HEADER_SIGNATURE)
    cols = header_name_to_col(rows[header_idx])

    lines: list[ParsedLine] = []
    for row in rows[header_idx + 1 :]:
        isin = row[cols["ISIN"]] if cols["ISIN"] < len(row) else None
        quantity = to_float(row[cols["Quantità"]]) if cols["Quantità"] < len(row) else None
        if not isin or quantity is None:
            break  # blank separator row before the totals block

        lines.append(
            ParsedLine(
                isin=str(isin).strip(),
                instrument_name=str(row[cols["Titolo"]]).strip(),
                ticker=str(row[cols["Simbolo"]]).strip() if row[cols["Simbolo"]] else None,
                market=str(row[cols["Mercato"]]).strip() if row[cols["Mercato"]] else None,
                currency=str(row[cols["Valuta"]]).strip() if row[cols["Valuta"]] else "EUR",
                quantity=quantity,
                avg_cost_price=to_float(row[cols["P.zo medio di carico"]]),
                cost_value=to_float(row[cols["Valore di carico"]]),
                market_price=to_float(row[cols["P.zo di mercato"]]),
                market_value=to_float(row[cols["Valore di mercato €"]]),
                unrealized_gain_loss=to_float(row[cols["Var €"]]),
                unrealized_gain_loss_pct=to_float(row[cols["Var%"]]),
                accrued_interest=to_float(row[cols["Rateo"]]),
                broker_fx_rate=to_float(row[cols["Cambio di mercato"]]),
            )
        )

    total_value_native = None
    for idx, row in enumerate(rows):
        if row and isinstance(row[0], str) and row[0].strip() == "Totale":
            totale_labels = header_name_to_col(row)
            if idx + 1 < len(rows) and "Valore di mercato" in totale_labels:
                total_value_native = to_float(rows[idx + 1][totale_labels["Valore di mercato"]])
            break

    return ParsedReport(
        broker="fineco",
        account_id=DEFAULT_ACCOUNT_ID,
        as_of_date=_as_of_date_from_filename(file_path),
        currency="EUR",
        total_value_native=total_value_native,
        source_file=file_path.name,
        lines=lines,
    )
