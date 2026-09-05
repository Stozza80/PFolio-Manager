# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A personal portfolio monitoring app, single user, local/LAN only. Broker exports
(Directa, Fineco, BNL) and market quotations get ingested into a local SQLite
database (`data/portfolio.db`), which a small read-only FastAPI backend
(`pfolio_manager/api.py`) exposes to a static HTML/JS dashboard (`frontend/`).

## Commands

```
# Refresh quotations / ingest any new broker report dropped in raw_reports/
.venv\Scripts\python.exe -m pfolio_manager.cli

# Full portfolio snapshot to Excel (data/exports/portfolio_snapshot_<date>.xlsx)
.venv\Scripts\python.exe -m pfolio_manager.export_portfolio

# API (http://localhost:8000, docs at /docs)
.venv\Scripts\python.exe -m uvicorn pfolio_manager.api:app --host 0.0.0.0 --port 8000

# Static frontend (http://localhost:8080) — API must also be running
cd frontend && python -m http.server 8080
```

There is no test suite, linter, or build step in this repo currently.

uvicorn's `--reload` has proven unreliable on Windows for this project (detects the
file change but doesn't actually restart) — after editing `pfolio_manager/api.py`,
kill and restart the process manually instead of trusting auto-reload.

## Architecture

### Data flow: `cli.py` is the only writer of `data/portfolio.db`

`python -m pfolio_manager.cli` (`run()` in `pfolio_manager/cli.py`) does one of two
things depending on whether `raw_reports/` has a new, not-yet-processed file (dedup
by SHA-256 against the `ingested_source` table):

- **New broker file present** → `detect.detect_broker()` picks the parser by
  structural signature (never by filename — account numbers/dates vary run to run;
  an unrecognized structure raises `DetectionError` rather than guessing) →
  `parsers.PARSERS[broker](file_path)` returns a `ParsedReport` → `store.ingest_report()`
  upserts instrument identity and **overwrites** (delete + reinsert) that
  account's `portfolio_history` rows for today with the report's quantity/avg_cost_price.
  The file is then archived to `raw_reports/processed/<date>/`. Accounts *not*
  included in this batch are left untouched (they keep whatever date their last
  known row has).
- **No new file** → `store.carry_forward_all()` copies every (instrument, account)
  pair's latest known quantity/avg_cost_price forward into a new row dated today.

Either branch then always runs the same two steps, which is the load-bearing
invariant of this codebase: **`quotes.refresh_all()` (yfinance / Borsa Italiana
scraper) is the only thing that may ever write to `quotations`, and
`store.revalue_portfolio_history()` recomputes `portfolio_history.market_value_*`
from that day's `quotations` close.** A broker-stated price must never leak into
`quotations` or into a stored market value — ingestion only supplies quantity and
cost basis. This is why `ingest_report()` inserts `market_value_native/eur` as
`NULL` and leaves pricing entirely to the subsequent refresh+revalue step.

The date every row for a given CLI run is stamped with comes from
`util.market_as_of_date()`: a deliberately simple heuristic (fetch timestamp minus
4h, then roll Saturday/Sunday back to Friday) rather than a real market calendar —
acceptable here because there's no intraday data and no short-term trading.

### Database (`pfolio_manager/db.py`, `data/portfolio.db`)

Five tables:
- `instrument` — broker-agnostic identity (ISIN unique, ticker, currency) + a
  `category_id` FK into `asset_category`, plus free-text `asset_subclass`.
  Report-derived fields (name/ticker/currency) are upserted by
  `store.get_or_create_instrument()`; curated fields (`category_id`,
  `asset_subclass`, eventually `short_name`) are only ever touched by
  `quotes.apply_curated_metadata()`, sourced from `config/isin_ticker_map.json` —
  never overwritten by ingestion, or curation would be wiped on every re-ingest.
- `asset_category` — macro-categories (currently "Stock" #eb5757, "Bonds"
  #49BEFF) each carrying the HTML color the frontend uses for both its pie-chart
  slice and its highlight badge. A category name not yet seen gets auto-created
  by `db.get_or_create_category()` with a neutral placeholder color
  (`DEFAULT_CATEGORY_COLOR`) that should then be curated by hand — this is how a
  future category (e.g. "Commodities") gets picked up without a schema change.
- `account` — one row per (broker, account_id) pair.
- `quotations` — one row per (instrument, date), `close` always a genuine
  per-unit price (MOT bond quotes are "per 100 nominal" at the source and get
  divided by 100 before being stored, so `quantity * close` is always a valid
  value computation with no special-casing by instrument type).
- `portfolio_history` — one row per (instrument, account, date): quantity,
  avg_cost_price (native currency, not yet back-filled historically — see the
  `avg_cost_price` column comment), market_value_native/eur.

### Quote curation (`config/isin_ticker_map.json`)

Every ISIN must be explicitly curated here before it can be priced — an
uncurated ISIN is flagged `unmapped` rather than guessed. An entry's `source`
selects the quote mechanism: `yfinance` (default, uses `ticker`), `mot_bond`
(Borsa Italiana MOT scraper, keyed by ISIN — bonds not listed on Yahoo Finance),
`borsaitaliana_fund` (Borsa Italiana fund page, `ticker` field holds the internal
fund code, not a real ticker), or `stockevents` (stockevents.app, ISIN-keyed).
All curated text (asset_class, asset_subclass, description, notes) is English —
project-wide convention, including this data layer.

### Parsers (`pfolio_manager/parsers/`)

Each broker module (`bnl.py`, `directa.py`, `fineco.py`) exposes `parse(file_path)
-> ParsedReport` and a `HEADER_SIGNATURE` set used by `detect.py` to identify the
format from the sheet's header row (first ~15 rows), independent of filename.
Shared helpers (`find_header_row`, `header_name_to_col`, `to_float`,
`ParserError`) live in `parsers/__init__.py`, which also defines the `PARSERS`
dict keyed by broker name.

### API (`pfolio_manager/api.py`)

Read-only FastAPI app, two endpoints, each doing its own SQL aggregation directly
against `data/portfolio.db` (no ORM anywhere in this project):
- `GET /api/dashboard` — everything the frontend's Dashboard page needs in one
  response (summary totals + day-over-day change, per-category allocation with
  colors, per-broker totals, 30-day value history, per-instrument holdings,
  per-subclass breakdown), all computed from the latest date in
  `portfolio_history` vs. the one before it.
- `GET /api/history?days=N` — longer/custom-range value series.

CORS is wide open (`allow_origins=["*"]`) by design — LAN-only, single-user,
unauthenticated, so origin restriction wouldn't add real protection.

### Frontend (`frontend/`)

Static HTML/CSS/JS based on PlainAdmin's free Bootstrap5+Chart.js admin
template, trimmed down (sidebar: Dashboard / History / Rebalancing — the latter
two are currently empty placeholder pages). `frontend/index.html`'s inline
`<script>` does `fetch(API_BASE + "/api/dashboard")` on load and renders every
card/chart/table from that response — there is no hardcoded portfolio data left
in the HTML. `API_BASE` (near the top of the script) currently points at
`http://localhost:8000`.

### Data confidentiality

`raw_reports/`, `data/portfolio.db`, `data/portfolio.json` (legacy, no longer
written), and `data/exports/` all contain real portfolio data and are
gitignored (`/data/*`, `/raw_reports/*` with `.gitkeep` exceptions) — only code
is committed. Don't propose removing these exclusions or committing anything
under those paths without the user explicitly asking. A pending idea (not yet
implemented) is to eventually split out a pure-market-quotes backup (no
holdings/quantities) that would be safe to commit, since that part is public
information — see git history / ask the user if this is still relevant.

## Known rough edges

- `pfolio_manager/models.py` still defines `Holding`, `Snapshot`,
  `DailySnapshot`, `IngestedSource`, and `holding_natural_key` — leftovers from
  a pre-SQLite JSON-file store. Only `ParsedLine` and `ParsedReport` are
  actually used by the current pipeline; the rest is dead code.
- `avg_cost_price` in `portfolio_history` is stored in the instrument's native
  currency and is not yet computed/back-filled historically in any special way
  — it's whatever the most recent broker report stated, carried forward as-is.
