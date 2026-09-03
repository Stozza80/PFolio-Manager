"""Load/save the normalized JSON store (data/portfolio.json): holdings, snapshots, ingested_sources."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import fx
from .models import SCHEMA_VERSION, Holding, ParsedReport, Snapshot, holding_natural_key

DEFAULT_STORE_PATH = Path("data/portfolio.json")


def _empty_store() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "holdings": [],
        "snapshots": [],
        "ingested_sources": [],
    }


def load(store_path: Path = DEFAULT_STORE_PATH) -> dict:
    if store_path.exists():
        return json.loads(store_path.read_text(encoding="utf-8"))
    return _empty_store()


def save(store: dict, store_path: Path = DEFAULT_STORE_PATH) -> None:
    store["generated_at"] = datetime.now(timezone.utc).isoformat()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(
        json.dumps(store, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )


def is_already_ingested(store: dict, file_hash: str) -> bool:
    return any(src["file_hash"] == file_hash for src in store["ingested_sources"])


def ingest_report(
    store: dict, report: ParsedReport, file_hash: str, cache_path: Path = fx.DEFAULT_CACHE_PATH
) -> dict:
    """Upsert holdings from a parsed report, append/replace its snapshot, record the source.

    Returns a summary dict (added/updated counts, computed vs. stated totals) for the CLI to report.
    """
    now = datetime.now(timezone.utc).isoformat()
    holdings_by_key = {h["holding_id"]: h for h in store["holdings"]}

    added = 0
    updated = 0
    snapshot_lines: list[dict] = []
    total_value_eur = 0.0
    any_unresolved = False

    for line in report.lines:
        key = holding_natural_key(report.broker, report.account_id, line.isin, line.instrument_name)

        market_value_eur, fx_rate_used, fx_rate_source = fx.resolve_eur_value(
            line.market_value, line.currency, report.as_of_date, line.broker_fx_rate, cache_path
        )

        if line.market_value is not None and market_value_eur is None:
            any_unresolved = True
        else:
            total_value_eur += market_value_eur or 0.0

        holding = Holding(
            holding_id=key,
            broker=report.broker,
            account_id=report.account_id,
            isin=line.isin,
            instrument_name=line.instrument_name,
            ticker=line.ticker,
            market=line.market,
            currency=line.currency,
            quantity=line.quantity,
            avg_cost_price=line.avg_cost_price,
            cost_value=line.cost_value,
            market_price=line.market_price,
            market_value=line.market_value,
            market_value_eur=market_value_eur,
            # Not derived in Phase 1: would need the historical FX rate at purchase time,
            # which none of the 3 brokers reliably expose (Fineco's "Cambio di carico" is
            # a candidate for a future enhancement, but isn't wired up here yet).
            cost_value_eur=None,
            unrealized_gain_loss=line.unrealized_gain_loss,
            unrealized_gain_loss_pct=line.unrealized_gain_loss_pct,
            accrued_interest=line.accrued_interest,
            fx_rate_used=fx_rate_used,
            fx_rate_source=fx_rate_source,
            as_of_date=report.as_of_date,
            source_file=report.source_file,
            parsed_at=now,
        ).to_dict()

        if key in holdings_by_key:
            updated += 1
        else:
            added += 1
        holdings_by_key[key] = holding
        snapshot_lines.append(holding)

    store["holdings"] = list(holdings_by_key.values())

    snapshot_id = f"{report.broker}:{report.account_id}:{report.as_of_date}"
    snapshot = Snapshot(
        snapshot_id=snapshot_id,
        broker=report.broker,
        account_id=report.account_id,
        as_of_date=report.as_of_date,
        total_value_native=report.total_value_native,
        total_value_eur=None if any_unresolved else total_value_eur,
        currency=report.currency,
        source_file=report.source_file,
        parsed_at=now,
        lines=snapshot_lines,
    ).to_dict()
    # Re-ingesting the same broker/account/date (e.g. a corrected re-export) replaces
    # the prior snapshot for that key rather than duplicating it.
    store["snapshots"] = [s for s in store["snapshots"] if s["snapshot_id"] != snapshot_id]
    store["snapshots"].append(snapshot)

    store["ingested_sources"].append(
        {
            "file_hash": file_hash,
            "file_name": report.source_file,
            "broker": report.broker,
            "ingested_at": now,
            "as_of_date": report.as_of_date,
        }
    )

    return {
        "broker": report.broker,
        "account_id": report.account_id,
        "as_of_date": report.as_of_date,
        "holdings_added": added,
        "holdings_updated": updated,
        "computed_total_eur": total_value_eur,
        "stated_total_native": report.total_value_native,
    }
