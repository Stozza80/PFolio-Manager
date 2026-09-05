# PFolio-Manager
Personal portfolio monitoring app: ingests broker reports and market quotations into a
local SQLite database, exposes them via a small API, and shows them in a local
dashboard (`frontend/`).

## Updating market quotations

Run the CLI from the repo root:

```
.venv\Scripts\python.exe -m pfolio_manager.cli
```

What it does depends on `raw_reports/`:
- If it contains a new, not-yet-processed broker report file (Directa/Fineco/BNL export), it gets ingested — see "Updating portfolio structure" below.
- Otherwise, it just refreshes market quotations (via Yahoo Finance / the Borsa Italiana scraper) for every instrument already in the database, carries every account's quantity/avg cost price forward unchanged, and stores a new `portfolio_history` snapshot dated today.

## Updating portfolio structure (new instruments, changed quantities)

A plain quotation refresh never changes quantities — it only re-prices what's already there. Whenever the actual portfolio changes (bought/sold an instrument, quantity changed), you need a fresh broker report:

1. Download/export an updated report from Directa, Fineco, or BNL's website.
2. Drop the file into `raw_reports/`.
3. Run the CLI (same command as above). It detects the new file, overwrites that broker/account's `portfolio_history` for today with the new quantities/instruments, refreshes quotations for the whole portfolio, then archives the processed file into `raw_reports/processed/<date>/`.

Only the broker(s) whose file you dropped in get updated that day — the others keep their last known quantities until either their own report is re-processed, or a plain quotation-refresh day carries everyone forward together.

If a brand-new instrument (never seen before) shows up in a report, curate its quote source and category in `config/isin_ticker_map.json` (see the existing entries for the expected format) so the next refresh can price it — quotes are never guessed or fabricated for an uncurated ISIN.

## Exporting a full portfolio snapshot to Excel

```
.venv\Scripts\python.exe -m pfolio_manager.export_portfolio
```

Writes `data/exports/portfolio_snapshot_<today>.xlsx` — one row per instrument/account as of the latest date in `portfolio_history` (broker, instrument, ISIN, category/subcategory, currency, quantity, avg cost price, market price, market value in native currency and EUR). Useful for a quick manual sanity check outside the app. Gitignored, like the rest of `data/`.

## Running the API

```
.venv\Scripts\python.exe -m uvicorn pfolio_manager.api:app --host 0.0.0.0 --port 8000
```

- `GET /api/dashboard` — everything the Dashboard page needs (summary, allocation, by-broker, 30-day history, holdings, subcategories).
- `GET /api/history?days=N` — longer/custom-range value history.
- `http://localhost:8000/docs` — interactive Swagger UI to explore both endpoints from a browser.

`--host 0.0.0.0` makes it reachable from other devices on the LAN, not just `localhost`.

Note: uvicorn's `--reload` has proven unreliable on Windows for this project (it detects file changes but doesn't always actually restart) — after editing `pfolio_manager/api.py`, stop and restart the server manually rather than relying on auto-reload.

## Running the frontend (dashboard)

```
cd frontend
python -m http.server 8080
```

Then open `http://localhost:8080` in a browser. The page fetches its data from the API above (`http://localhost:8000` by default — see `API_BASE` near the top of the `<script>` block in `frontend/index.html`), so the API must be running at the same time.
