"""One-off export: `python -m pfolio_manager.export_portfolio`.

Writes an Excel file with a full snapshot of the current portfolio (the latest
date present in `portfolio_history`) for manual inspection — one row per
instrument/account. Output goes to data/exports/ (already gitignored via
/data/*) since it contains real portfolio data.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from . import db

OUTPUT_DIR = Path("data/exports")
HEADERS = [
    "Broker",
    "Instrument",
    "ISIN",
    "Category",
    "Subcategory",
    "Currency",
    "Quantity",
    "Avg Cost Price",
    "Market Price",
    "Market Value (Native)",
    "Market Value (EUR)",
    "As Of Date",
]


def build_workbook(conn: sqlite3.Connection) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Portfolio"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    latest = conn.execute("SELECT MAX(date) FROM portfolio_history").fetchone()[0]
    if latest is None:
        return wb

    rows = conn.execute(
        """
        SELECT a.broker AS broker, COALESCE(i.short_name, i.full_name) AS name, i.isin AS isin,
               c.name AS category, i.asset_subclass AS subclass, i.currency AS currency,
               ph.quantity AS quantity, ph.avg_cost_price AS avg_cost_price,
               q.close AS market_price, ph.market_value_native AS market_value_native,
               ph.market_value_eur AS market_value_eur, ph.date AS as_of_date
        FROM portfolio_history ph
        JOIN instrument i ON i.id = ph.instrument_id
        JOIN account a ON a.id = ph.account_id
        LEFT JOIN asset_category c ON c.id = i.category_id
        LEFT JOIN quotations q ON q.instrument_id = ph.instrument_id AND q.date = ph.date
        WHERE ph.date = ?
        ORDER BY a.broker, name
        """,
        (latest,),
    ).fetchall()

    for r in rows:
        ws.append(list(r))

    for column_cells in ws.columns:
        length = max(len(str(c.value)) if c.value is not None else 0 for c in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 10), 60)

    return wb


def main() -> int:
    conn = db.get_connection()
    try:
        conn.row_factory = sqlite3.Row
        wb = build_workbook(conn)
    finally:
        conn.close()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"portfolio_snapshot_{date.today().isoformat()}.xlsx"
    wb.save(out_path)
    print(f"Written {out_path}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
