"""CLI entry point: `python -m pfolio_manager.cli`.

Orchestrates: scan raw_reports/ for new files (by content hash) -> detect broker -> parse
-> resolve FX -> upsert holdings + append snapshot -> archive the file. If no new files are
found, refreshes quotes for all existing holdings instead. Always prints a structured summary
(the portfolio-update skill relays this back to the user).
"""
from __future__ import annotations

import shutil
import sys
from datetime import date
from pathlib import Path

from . import detect, quotes, store, util
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


def run() -> int:
    portfolio = store.load()
    files = _scan_report_files(RAW_REPORTS_DIR)

    new_files = []
    for f in files:
        file_hash = util.sha256_file(f)
        if not store.is_already_ingested(portfolio, file_hash):
            new_files.append((f, file_hash))

    if new_files:
        print(f"Trovati {len(new_files)} nuovi file da elaborare in raw_reports/.")
        ingestion_summaries = []
        errors = []
        for file_path, file_hash in new_files:
            try:
                broker = detect.detect_broker(file_path)
                report = PARSERS[broker](file_path)
                summary = store.ingest_report(portfolio, report, file_hash)
                ingestion_summaries.append(summary)
                _archive_file(file_path, PROCESSED_DIR)
            except (detect.DetectionError, ParserError) as exc:
                errors.append((file_path.name, str(exc)))

        store.save(portfolio)

        print("\n=== Riepilogo ingestion ===")
        for s in ingestion_summaries:
            stated = s["stated_total_native"]
            computed = s["computed_total_eur"]
            mismatch = ""
            if stated is not None:
                diff = computed - stated
                if abs(diff) > 0.5:
                    mismatch = f"  [attenzione: differenza {diff:+.2f} tra totale calcolato e dichiarato dal broker]"
            print(
                f"- {s['broker']} ({s['account_id']}) al {s['as_of_date']}: "
                f"{s['holdings_added']} nuovi, {s['holdings_updated']} aggiornati, "
                f"totale calcolato EUR {computed:.2f} vs dichiarato {stated}{mismatch}"
            )
        if errors:
            print("\n=== File non elaborati (errore) ===")
            for name, msg in errors:
                print(f"- {name}: {msg}")
    else:
        print("Nessun nuovo file trovato in raw_reports/ — aggiorno solo le quotazioni.")
        quote_summary = quotes.refresh_all(portfolio["holdings"])
        store.save(portfolio)

        print("\n=== Riepilogo refresh quotazioni ===")
        print(
            f"ok: {quote_summary['ok']}, non mappati: {quote_summary['unmapped']}, "
            f"lookup falliti: {quote_summary['lookup_failed']}"
        )
        if quote_summary["unmapped"] or quote_summary["lookup_failed"]:
            print("Strumenti da controllare in config/isin_ticker_map.json:")
            for h in portfolio["holdings"]:
                if h.get("quote_status") in ("unmapped", "lookup_failed"):
                    print(f"  - [{h['quote_status']}] {h.get('isin')} {h.get('instrument_name')}")

    return 0


if __name__ == "__main__":
    sys.exit(run())
