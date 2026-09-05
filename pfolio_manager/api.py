"""Read-only API serving the dashboard report's data from data/portfolio.db.

Run with: `uvicorn pfolio_manager.api:app --reload` (dev) from the repo root, or
without --reload for a stable local/LAN server. Two endpoints, one per report
section (see project notes — more sections/endpoints will be added over time as
the report grows beyond the current single Dashboard page):

- GET /api/dashboard: everything the Dashboard page needs in one response.
- GET /api/history?days=N: a longer/custom-range value series, for the (not yet
  built) History page.

CORS is wide open (`allow_origins=["*"]`) — this is a LAN-only, single-user,
unauthenticated tool by design (see the project's data-confidentiality notes),
not a public API, so origin restriction wouldn't add real protection here.
"""
from __future__ import annotations

import sqlite3

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import db

app = FastAPI(title="PFolio Manager API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _get_conn() -> sqlite3.Connection:
    conn = db.get_connection()
    conn.row_factory = sqlite3.Row
    return conn


def _latest_dates(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM portfolio_history ORDER BY date DESC LIMIT 2"
    ).fetchall()
    dates = [r[0] for r in rows]
    latest = dates[0] if dates else None
    previous = dates[1] if len(dates) > 1 else None
    return latest, previous


def _total_for_date(conn: sqlite3.Connection, as_of_date: str) -> float:
    row = conn.execute(
        "SELECT SUM(market_value_eur) FROM portfolio_history WHERE date = ?", (as_of_date,)
    ).fetchone()
    return row[0] or 0.0


def _category_totals(conn: sqlite3.Connection, as_of_date: str) -> dict[int, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT c.id AS id, c.name AS name, c.color AS color, SUM(ph.market_value_eur) AS total
        FROM portfolio_history ph
        JOIN instrument i ON i.id = ph.instrument_id
        JOIN asset_category c ON c.id = i.category_id
        WHERE ph.date = ?
        GROUP BY c.id
        """,
        (as_of_date,),
    ).fetchall()
    return {r["id"]: r for r in rows}


def _history_series(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT date, SUM(market_value_eur) AS total
        FROM portfolio_history
        GROUP BY date
        ORDER BY date DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [{"date": r["date"], "total_eur": round(r["total"], 2)} for r in rows][::-1]


@app.get("/api/dashboard")
def get_dashboard():
    conn = _get_conn()
    try:
        latest, previous = _latest_dates(conn)
        if latest is None:
            return {"as_of_date": None}

        portfolio_total = _total_for_date(conn, latest)
        previous_total = _total_for_date(conn, previous) if previous else None
        change_eur = (portfolio_total - previous_total) if previous_total is not None else None
        change_pct = (
            round((change_eur / previous_total) * 100, 2)
            if change_eur is not None and previous_total
            else None
        )

        latest_categories = _category_totals(conn, latest)
        previous_categories = _category_totals(conn, previous) if previous else {}
        allocation = []
        for cid, r in latest_categories.items():
            prev_row = previous_categories.get(cid)
            prev_total = prev_row["total"] if prev_row else None
            cat_change_eur = (r["total"] - prev_total) if prev_total is not None else None
            cat_change_pct = (
                round((cat_change_eur / prev_total) * 100, 2)
                if cat_change_eur is not None and prev_total
                else None
            )
            allocation.append(
                {
                    "label": r["name"],
                    "color": r["color"],
                    "value_eur": round(r["total"], 2),
                    "change_eur": round(cat_change_eur, 2) if cat_change_eur is not None else None,
                    "change_pct": cat_change_pct,
                }
            )
        allocation.sort(key=lambda a: a["value_eur"], reverse=True)

        by_broker = [
            {"broker": r["broker"], "value_eur": round(r["total"], 2)}
            for r in conn.execute(
                """
                SELECT a.broker AS broker, SUM(ph.market_value_eur) AS total
                FROM portfolio_history ph
                JOIN account a ON a.id = ph.account_id
                WHERE ph.date = ?
                GROUP BY a.id
                ORDER BY total DESC
                """,
                (latest,),
            ).fetchall()
        ]

        holdings_rows = conn.execute(
            """
            SELECT COALESCE(i.short_name, i.full_name) AS name, i.isin AS isin,
                   c.name AS category, c.color AS category_color,
                   SUM(ph.market_value_eur) AS value_eur
            FROM portfolio_history ph
            JOIN instrument i ON i.id = ph.instrument_id
            LEFT JOIN asset_category c ON c.id = i.category_id
            WHERE ph.date = ?
            GROUP BY i.id
            ORDER BY value_eur DESC
            """,
            (latest,),
        ).fetchall()
        holdings = [
            {
                "name": r["name"],
                "isin": r["isin"],
                "category": r["category"],
                "category_color": r["category_color"],
                "value_eur": round(r["value_eur"], 2),
                "pct": round((r["value_eur"] / portfolio_total) * 100, 1) if portfolio_total else None,
            }
            for r in holdings_rows
        ]

        subclass_rows = conn.execute(
            """
            SELECT i.asset_subclass AS subclass, c.name AS category, c.color AS category_color,
                   SUM(ph.market_value_eur) AS value_eur
            FROM portfolio_history ph
            JOIN instrument i ON i.id = ph.instrument_id
            LEFT JOIN asset_category c ON c.id = i.category_id
            WHERE ph.date = ?
            GROUP BY i.asset_subclass
            ORDER BY value_eur DESC
            """,
            (latest,),
        ).fetchall()
        subcategories = [
            {
                "subcategory": r["subclass"],
                "category_color": r["category_color"],
                "value_eur": round(r["value_eur"], 2),
                "pct": round((r["value_eur"] / portfolio_total) * 100, 1) if portfolio_total else None,
            }
            for r in subclass_rows
        ]

        return {
            "as_of_date": latest,
            "summary": {
                "portfolio_total_eur": round(portfolio_total, 2),
                "change_eur": round(change_eur, 2) if change_eur is not None else None,
                "change_pct": change_pct,
            },
            "allocation": allocation,
            "by_broker": by_broker,
            "history": _history_series(conn, 30),
            "holdings": holdings,
            "subcategories": subcategories,
        }
    finally:
        conn.close()


@app.get("/api/history")
def get_history(days: int = 90):
    conn = _get_conn()
    try:
        return {"history": _history_series(conn, days)}
    finally:
        conn.close()
