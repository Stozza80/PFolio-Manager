"""SQLite schema and connection helper for the portfolio store (data/portfolio.db).

Five tables: `asset_category` (curated macro-categories — Stock, Bonds, and
whatever gets added later — each with an HTML color the frontend uses for both
its pie-chart slice and its highlight badge), `instrument` (static
identity/metadata, broker-agnostic, FK to asset_category; asset_subclass stays
free text since it's much more granular/varied), `account` (broker +
account_id pairs), `quotations` (daily close price per instrument — populated
only from quotes.refresh_all(), never from a broker report), and
`portfolio_history` (daily quantity/value per instrument+account).

All curated text (category names, subclasses, everything user-facing) is
English — this project's language throughout, including the data layer.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("data/portfolio.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS asset_category (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instrument (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    isin TEXT UNIQUE,
    short_name TEXT,
    full_name TEXT NOT NULL,
    ticker TEXT,
    category_id INTEGER REFERENCES asset_category (id),
    asset_subclass TEXT,
    currency TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker TEXT NOT NULL,
    account_id TEXT NOT NULL,
    UNIQUE (broker, account_id)
);

CREATE TABLE IF NOT EXISTS quotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instrument (id),
    date DATE NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL NOT NULL,
    volume REAL,
    UNIQUE (instrument_id, date)
);

CREATE TABLE IF NOT EXISTS portfolio_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id INTEGER NOT NULL REFERENCES instrument (id),
    account_id INTEGER NOT NULL REFERENCES account (id),
    date DATE NOT NULL,
    quantity REAL NOT NULL,
    avg_cost_price REAL,
    market_value_native REAL,
    market_value_eur REAL,
    UNIQUE (instrument_id, account_id, date)
);

CREATE TABLE IF NOT EXISTS ingested_source (
    file_hash TEXT PRIMARY KEY,
    file_name TEXT NOT NULL,
    broker TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_date DATE NOT NULL
);
"""

# Known categories seeded on init, with the colors already used across the report's
# pie charts and highlight badges. New categories (e.g. "Commodities" if gold gets
# added) aren't seeded here — quotes.get_or_create_category() will auto-create them
# with a neutral placeholder color that should then be curated by hand.
SEED_CATEGORIES = [
    ("Stock", "#eb5757"),
    ("Bonds", "#49BEFF"),
]

DEFAULT_CATEGORY_COLOR = "#8F92A1"  # neutral gray placeholder for auto-created categories


def get_connection(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    conn = get_connection(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT OR IGNORE INTO asset_category (name, color) VALUES (?, ?)", SEED_CATEGORIES
        )
        conn.commit()
    finally:
        conn.close()


def get_or_create_category(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM asset_category WHERE name = ?", (name,)).fetchone()
    if row:
        return row[0]
    conn.execute(
        "INSERT INTO asset_category (name, color) VALUES (?, ?)", (name, DEFAULT_CATEGORY_COLOR)
    )
    return conn.execute("SELECT id FROM asset_category WHERE name = ?", (name,)).fetchone()[0]
