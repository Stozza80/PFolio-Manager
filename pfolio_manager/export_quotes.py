"""One-off export: `python -m pfolio_manager.export_quotes`.

Writes a simple Excel file listing every holding with its current quote (native
currency) and that quote converted to EUR, for a quick manual sanity check. Output goes
to data/exports/ (already gitignored via /data/*) since it contains real portfolio data.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

from . import store

OUTPUT_DIR = Path("data/exports")
HEADERS = [
    "Broker",
    "Strumento",
    "ISIN",
    "Asset Class",
    "Sottocategoria",
    "Valuta",
    "Quotazione",
    "Quotazione (EUR)",
    "Controvalore (EUR)",
    "Aggiornata al",
    "Descrizione",
]


def build_workbook(holdings: list[dict]) -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Quotazioni"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for h in sorted(holdings, key=lambda h: (h.get("broker", ""), h.get("instrument_name", ""))):
        price = h.get("market_price")
        fx_rate = h.get("fx_rate_used")
        price_eur = price * fx_rate if price is not None and fx_rate is not None else None
        ws.append(
            [
                h.get("broker"),
                h.get("instrument_name"),
                h.get("isin"),
                h.get("asset_class"),
                h.get("asset_subclass"),
                h.get("currency"),
                price,
                price_eur,
                h.get("market_value_eur"),
                h.get("quote_last_refreshed_at") or h.get("as_of_date"),
                h.get("description"),
            ]
        )

    for column_cells in ws.columns:
        length = max(len(str(c.value)) if c.value is not None else 0 for c in column_cells)
        ws.column_dimensions[column_cells[0].column_letter].width = min(max(length + 2, 10), 60)

    return wb


def main() -> int:
    portfolio = store.load()
    wb = build_workbook(portfolio["holdings"])

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"quotazioni_{date.today().isoformat()}.xlsx"
    wb.save(out_path)
    print(f"Scritto {out_path} ({len(portfolio['holdings'])} strumenti).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
