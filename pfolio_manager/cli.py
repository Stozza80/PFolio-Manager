"""CLI entry point: `python -m pfolio_manager.cli`.

Orchestrates against data/portfolio.db (SQLite): scan raw_reports/ for new files (by
content hash) -> detect broker -> parse -> upsert instrument identity + overwrite that
account's portfolio_history for today. If no new files are found, carries every
account's latest known quantity/avg_cost_price forward to today instead. Either way,
quotes.refresh_all() then runs for every instrument (quotations always come from the
same online source, never a broker-stated price), and portfolio_history for today is
revalued from those fresh quotations. Always prints a structured summary (the
portfolio-update skill relays this back to the user).
"""
from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

from . import db, detect, quotes, store, util
from .parsers import PARSERS, ParserError

RAW_REPORTS_DIR = Path("raw_reports")
PROCESSED_DIR = RAW_REPORTS_DIR / "processed"
REPORT_EXTENSIONS = {".xlsx", ".xls"}


def _scan_report_files(raw_dir: Path) -> list[Path]:
    if not raw_dir.exists():
        return []
    return sorted(
        p for p in raw_dir.iterdir() if p.is_file() and p.suffix.lower() in REPORT_EXTENSIONS
    )


def _archive_file(file_path: Path, processed_dir: Path) -> Path:
    dest_dir = processed_dir / date.today().isoformat()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / file_path.name
    shutil.move(str(file_path), str(dest))
    return dest


def _print_flagged(quote_summary: dict) -> None:
    if quote_summary["unmapped"] or quote_summary["lookup_failed"]:
        print("Strumenti da controllare in config/isin_ticker_map.json:")
        for isin, name, status in quote_summary["flagged"]:
            print(f"  - [{status}] {isin} {name}")


def run() -> int:
    db.init_db()
    conn = db.get_connection()
    as_of_date = util.market_as_of_date().isoformat()

    files = _scan_report_files(RAW_REPORTS_DIR)
    new_files = []
    for f in files:
        file_hash = util.sha256_file(f)
        if not store.is_already_ingested(conn, file_hash):
            new_files.append((f, file_hash))

    if new_files:
        print(f"Trovati {len(new_files)} nuovi file da elaborare in raw_reports/.")
        ingestion_summaries = []
        errors = []
        for file_path, file_hash in new_files:
            try:
                broker = detect.detect_broker(file_path)
                report = PARSERS[broker](file_path)
                summary = store.ingest_report(conn, report, file_hash, as_of_date)
                ingestion_summaries.append(summary)
                _archive_file(file_path, PROCESSED_DIR)
            except (detect.DetectionError, ParserError) as exc:
                errors.append((file_path.name, str(exc)))

        # Ingestion only wrote instrument identity/quantity/cost from the broker
        # report; the price itself must always come from the same online source
        # (never the broker-stated price), so quotations stay consistent day to day.
        quote_summary = quotes.refresh_all(conn, as_of_date)
        quotes.apply_curated_metadata(conn)
        store.revalue_portfolio_history(conn, as_of_date)
        conn.commit()

        print("\n=== Riepilogo ingestion ===")
        for s in ingestion_summaries:
            stated = s["stated_total_native"]
            computed = store.portfolio_total_eur(conn, as_of_date)
            print(
                f"- {s['broker']} ({s['account_id']}) al {s['as_of_date']}: "
                f"{s['lines']} strumenti, totale portafoglio EUR {computed:.2f} "
                f"(dichiarato dal broker per questo conto: {stated})"
            )
        if errors:
            print("\n=== File non elaborati (errore) ===")
            for name, msg in errors:
                print(f"- {name}: {msg}")
        print(
            f"\nQuotazioni: ok {quote_summary['ok']}, non mappati {quote_summary['unmapped']}, "
            f"lookup falliti {quote_summary['lookup_failed']}"
        )
        _print_flagged(quote_summary)
    else:
        print("Nessun nuovo file trovato in raw_reports/ — aggiorno solo le quotazioni.")
        carried = store.carry_forward_all(conn, as_of_date)
        quote_summary = quotes.refresh_all(conn, as_of_date)
        quotes.apply_curated_metadata(conn)
        store.revalue_portfolio_history(conn, as_of_date)
        conn.commit()

        total = store.portfolio_total_eur(conn, as_of_date)
        print(f"\n=== Riepilogo refresh quotazioni ({as_of_date}) ===")
        print(f"Posizioni riportate avanti: {carried}. Totale portafoglio EUR: {total:.2f}")
        print(
            f"ok: {quote_summary['ok']}, non mappati: {quote_summary['unmapped']}, "
            f"lookup falliti: {quote_summary['lookup_failed']}"
        )
        _print_flagged(quote_summary)

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(run())
