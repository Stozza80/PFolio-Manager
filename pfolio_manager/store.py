"""SQLite-backed portfolio store (data/portfolio.db): instrument/account identity,
ingestion bookkeeping, and portfolio_history read/write. Quote prices themselves
live in `quotations` and are only ever written by `quotes.refresh_all()` — never
from a broker-stated price (see `ingest_report`, which leaves valuation to the
caller's subsequent `revalue_portfolio_history` call).
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from . import fx
from .models import ParsedReport


def is_already_ingested(conn: sqlite3.Connection, file_hash: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ingested_source WHERE file_hash = ?", (file_hash,)
    ).fetchone()
    return row is not None


def record_ingested_source(
    conn: sqlite3.Connection, file_hash: str, file_name: str, broker: str, as_of_date: str
) -> None:
    conn.execute(
        """
        INSERT INTO ingested_source (file_hash, file_name, broker, ingested_at, as_of_date)
        VALUES (?, ?, ?, ?, ?)
        """,
        (file_hash, file_name, broker, datetime.now(timezone.utc).isoformat(), as_of_date),
    )


def get_or_create_instrument(
    conn: sqlite3.Connection,
    isin: str,
    full_name: str,
    ticker: str | None,
    currency: str,
) -> int:
    """Upsert an instrument's report-derived identity fields (name/ticker/currency).

    Deliberately does NOT touch `asset_class`/`asset_subclass`/`short_name` — those
    are curated metadata (see quotes.apply_curated_metadata), not something a broker
    report or a quote refresh knows about; overwriting them here would wipe curation
    on every re-ingestion.
    """
    conn.execute(
        """
        INSERT INTO instrument (isin, full_name, ticker, currency)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(isin) DO UPDATE SET
            full_name=excluded.full_name,
            ticker=excluded.ticker,
            currency=excluded.currency
        """,
        (isin, full_name, ticker, currency),
    )
    row = conn.execute("SELECT id FROM instrument WHERE isin = ?", (isin,)).fetchone()
    return row[0]


def get_or_create_account(conn: sqlite3.Connection, broker: str, account_id: str) -> int:
    conn.execute(
        "INSERT OR IGNORE INTO account (broker, account_id) VALUES (?, ?)", (broker, account_id)
    )
    row = conn.execute(
        "SELECT id FROM account WHERE broker = ? AND account_id = ?", (broker, account_id)
    ).fetchone()
    return row[0]


def ingest_report(
    conn: sqlite3.Connection,
    report: ParsedReport,
    file_hash: str,
    as_of_date: str,
    cache_path=fx.DEFAULT_CACHE_PATH,
) -> dict:
    """Upsert instrument identity and overwrite this account's portfolio_history for
    `as_of_date` from a freshly parsed broker report. Quantity/avg_cost_price come from
    the report; market_value is left NULL here — the caller must run quotes.refresh_all()
    and then revalue_portfolio_history() afterwards so valuation always comes from the
    same online quote source, never the broker-stated price.
    """
    account_pk = get_or_create_account(conn, report.broker, report.account_id)

    conn.execute(
        "DELETE FROM portfolio_history WHERE account_id = ? AND date = ?",
        (account_pk, as_of_date),
    )

    for line in report.lines:
        if not line.isin:
            continue
        instrument_id = get_or_create_instrument(
            conn,
            isin=line.isin,
            full_name=line.instrument_name,
            ticker=line.ticker,
            currency=line.currency,
        )
        conn.execute(
            """
            INSERT INTO portfolio_history
                (instrument_id, account_id, date, quantity, avg_cost_price,
                 market_value_native, market_value_eur)
            VALUES (?, ?, ?, ?, ?, NULL, NULL)
            """,
            (instrument_id, account_pk, as_of_date, line.quantity, line.avg_cost_price),
        )

    record_ingested_source(conn, file_hash, report.source_file, report.broker, as_of_date)

    return {
        "broker": report.broker,
        "account_id": report.account_id,
        "as_of_date": as_of_date,
        "lines": len(report.lines),
        "stated_total_native": report.total_value_native,
    }


def carry_forward_all(conn: sqlite3.Connection, as_of_date: str) -> int:
    """Refresh-only path: copy each account's latest known snapshot (every
    instrument it held as of that account's own most recent date) forward into a
    new `as_of_date` row per instrument (market_value left NULL, filled in later by
    revalue_portfolio_history).

    Carrying forward per-account's single latest date — rather than each
    (instrument, account) pair's own latest date independently — matters because a
    broker re-ingestion deletes and reinserts that account's entire row set for its
    date, so an instrument absent from the new report (sold) simply has no row on
    that date. Using the pair's own latest date would let a later carry-forward
    reach past that date to an older row and resurrect the sold position; anchoring
    on the account's latest date means a sold instrument, having no row there, is
    never carried forward again.

    Skips accounts that already have *any* row for `as_of_date` (i.e. accounts just
    ingested this run) entirely, so carry-forward never touches a just-ingested
    account's data.
    """
    touched_accounts = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT account_id FROM portfolio_history WHERE date = ?",
            (as_of_date,),
        ).fetchall()
    }

    latest_rows = conn.execute(
        """
        SELECT ph.instrument_id, ph.account_id, ph.quantity, ph.avg_cost_price
        FROM portfolio_history ph
        INNER JOIN (
            SELECT account_id, MAX(date) AS max_date
            FROM portfolio_history
            WHERE date < ?
            GROUP BY account_id
        ) latest
        ON ph.account_id = latest.account_id
        AND ph.date = latest.max_date
        """,
        (as_of_date,),
    ).fetchall()

    carried = 0
    for instrument_id, account_id, quantity, avg_cost_price in latest_rows:
        if account_id in touched_accounts:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO portfolio_history
                (instrument_id, account_id, date, quantity, avg_cost_price,
                 market_value_native, market_value_eur)
            VALUES (?, ?, ?, ?, ?, NULL, NULL)
            """,
            (instrument_id, account_id, as_of_date, quantity, avg_cost_price),
        )
        carried += 1
    return carried


def revalue_portfolio_history(
    conn: sqlite3.Connection, as_of_date: str, cache_path=fx.DEFAULT_CACHE_PATH
) -> dict:
    """Fill in market_value_native/market_value_eur for every portfolio_history row
    dated `as_of_date`, using that day's `quotations` close (never the broker price).
    Rows for instruments with no quotation yet (unmapped/lookup_failed) are left NULL.
    """
    rows = conn.execute(
        """
        SELECT ph.id, ph.instrument_id, ph.quantity, i.currency
        FROM portfolio_history ph
        INNER JOIN instrument i ON i.id = ph.instrument_id
        WHERE ph.date = ?
        """,
        (as_of_date,),
    ).fetchall()

    revalued = 0
    missing_quote = 0
    for row_id, instrument_id, quantity, currency in rows:
        quote = conn.execute(
            "SELECT close FROM quotations WHERE instrument_id = ? AND date = ?",
            (instrument_id, as_of_date),
        ).fetchone()
        if quote is None:
            missing_quote += 1
            continue
        market_value_native = quantity * quote[0]
        market_value_eur, _rate, _source = fx.resolve_eur_value(
            market_value_native, currency, as_of_date, cache_path=cache_path
        )
        conn.execute(
            "UPDATE portfolio_history SET market_value_native = ?, market_value_eur = ? WHERE id = ?",
            (market_value_native, market_value_eur, row_id),
        )
        revalued += 1

    return {"revalued": revalued, "missing_quote": missing_quote}


def portfolio_total_eur(conn: sqlite3.Connection, as_of_date: str) -> float:
    row = conn.execute(
        "SELECT SUM(market_value_eur) FROM portfolio_history WHERE date = ?", (as_of_date,)
    ).fetchone()
    return row[0] or 0.0
