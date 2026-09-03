"""Market-quote refresh via Yahoo Finance (yfinance) or the Borsa Italiana MOT scraper
(pfolio_manager.scraper), keyed by a curated ISIN->quote-source map.

Never guesses a ticker/source from an ISIN or broker-supplied symbol: an ISIN not yet
present in the mapping file gets a `needs_mapping` placeholder entry (and the holding is
flagged `unmapped`) so the user can curate it over time, rather than silently failing or
fabricating a lookup. Confirmed during development that not all instruments are on Yahoo
Finance (e.g. some Frankfurt-listed ETF tickers 404, single bonds generally aren't there
at all) — those surface as `lookup_failed`/`unmapped`, never as a stale or fabricated price.
Bonds not on Yahoo Finance are often findable via the MOT scraper instead (source
"mot_bond" in the mapping entry), which needs no ticker — just the ISIN.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yfinance as yf

from . import fx, scraper

DEFAULT_MAP_PATH = Path("config/isin_ticker_map.json")


def load_map(map_path: Path = DEFAULT_MAP_PATH) -> dict:
    if map_path.exists():
        return json.loads(map_path.read_text(encoding="utf-8"))
    return {}


def save_map(mapping: dict, map_path: Path = DEFAULT_MAP_PATH) -> None:
    map_path.parent.mkdir(parents=True, exist_ok=True)
    map_path.write_text(
        json.dumps(mapping, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )


def _get_mapped_entry(isin: str, instrument_name: str, mapping: dict) -> tuple[Optional[dict], bool]:
    """Return (entry_or_None, mapping_changed). None means not yet curated by the user.

    An entry's "source" field selects the quote mechanism: "yfinance" (default, uses
    "ticker") or "mot_bond" (uses Borsa Italiana's MOT scraper, keyed by ISIN directly —
    "ticker" is unused for that source).
    """
    entry = mapping.get(isin)
    if entry is None:
        mapping[isin] = {
            "ticker": None,
            "source": "yfinance",
            "name": instrument_name,
            "status": "needs_mapping",
            "notes": "",
        }
        return None, True
    if entry.get("status") != "mapped":
        return None, False
    return entry, False


def _fetch_price(ticker: str) -> tuple[Optional[float], Optional[str]]:
    """Return (last_price, currency) for a Yahoo Finance ticker, or (None, None) on failure.

    Some instruments trade on Yahoo Finance in a different currency than the one the
    broker reports (e.g. a USD-denominated LSE listing for a fund the broker books in
    EUR) — the caller must convert using the returned currency, not assume it matches.
    """
    try:
        fast_info = yf.Ticker(ticker).fast_info
        price = fast_info["last_price"]
        if price:
            return float(price), fast_info["currency"]
    except Exception:
        pass
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="1d")
        if not hist.empty:
            currency = None
            try:
                currency = t.fast_info["currency"]
            except Exception:
                pass
            return float(hist["Close"].iloc[-1]), currency
    except Exception:
        pass
    return None, None


def _convert_price_currency(
    price: float, from_currency: str, to_currency: str, as_of_date: str, cache_path: Path
) -> Optional[float]:
    """Convert a price quoted in `from_currency` into `to_currency`, bridging via EUR."""
    if from_currency == to_currency:
        return price
    value_eur, _rate, source = fx.resolve_eur_value(price, from_currency, as_of_date, cache_path=cache_path)
    if value_eur is None:
        return None
    if to_currency == "EUR":
        return value_eur
    to_eur_rate, _source = fx.get_eur_rate(to_currency, as_of_date, cache_path)
    if not to_eur_rate:
        return None
    return value_eur / to_eur_rate


def refresh_holding(holding: dict, mapping: dict, cache_path: Path = fx.DEFAULT_CACHE_PATH) -> bool:
    """Refresh a single holding dict in place. Returns True if the mapping dict was changed."""
    isin = holding.get("isin")
    if not isin:
        holding["quote_status"] = "unmapped"
        return False

    entry, mapping_changed = _get_mapped_entry(isin, holding.get("instrument_name", ""), mapping)
    if entry is None:
        holding["quote_status"] = "unmapped"
        holding["yfinance_ticker"] = None
        return mapping_changed

    source = entry.get("source", "yfinance")
    as_of_date = datetime.now(timezone.utc).date().isoformat()
    holding_currency = holding.get("currency", "EUR")

    if source == "mot_bond":
        quote_ref = f"MOT:{isin}"
        raw_price, price_currency = scraper.fetch_mot_bond_quote(isin)
        # MOT bond prices are quoted per 100 nominal; our "quantity" for bonds is the
        # nominal face value, so market_value = quantity * (price / 100) — confirmed
        # against the broker reports, which reproduce their stated value this way.
        price = raw_price / 100 if raw_price is not None else None
    elif source == "borsaitaliana_fund":
        fund_code = entry.get("ticker")  # holds the Borsa Italiana internal fund code
        if not fund_code:
            holding["quote_status"] = "unmapped"
            holding["yfinance_ticker"] = None
            return mapping_changed
        quote_ref = f"BIT-FONDI:{fund_code}"
        price, price_currency = scraper.fetch_borsaitaliana_fund_quote(fund_code)
    elif source == "stockevents":
        quote_ref = f"stockevents:{isin}"
        price, price_currency = scraper.fetch_stockevents_quote(isin)
    else:
        ticker = entry.get("ticker")
        if not ticker:
            holding["quote_status"] = "unmapped"
            holding["yfinance_ticker"] = None
            return mapping_changed
        quote_ref = ticker
        price, price_currency = _fetch_price(ticker)

    if price is None:
        holding["quote_status"] = "lookup_failed"
        holding["yfinance_ticker"] = quote_ref
        return mapping_changed

    if price_currency and price_currency != holding_currency:
        converted = _convert_price_currency(price, price_currency, holding_currency, as_of_date, cache_path)
        if converted is None:
            holding["quote_status"] = "lookup_failed"
            holding["yfinance_ticker"] = quote_ref
            return mapping_changed
        price = converted

    quantity = holding.get("quantity") or 0.0
    market_value = quantity * price
    market_value_eur, fx_rate_used, fx_rate_source = fx.resolve_eur_value(
        market_value, holding_currency, as_of_date, cache_path=cache_path
    )

    # market_price is meant to read like the broker's own quote convention: for bonds
    # that's "per 100 nominal" (e.g. 102.10, not 1.021) — currency conversion above was
    # already applied to the per-unit `price`, so scaling back up by 100 here still
    # reflects the converted value (the two operations commute).
    display_price = price * 100 if source == "mot_bond" else price

    holding["market_price"] = display_price
    holding["market_value"] = market_value
    holding["market_value_eur"] = market_value_eur
    holding["fx_rate_used"] = fx_rate_used
    holding["fx_rate_source"] = fx_rate_source
    holding["quote_last_refreshed_at"] = datetime.now(timezone.utc).isoformat()
    holding["quote_source"] = source
    holding["yfinance_ticker"] = quote_ref
    holding["quote_status"] = "ok"
    return mapping_changed


def refresh_all(
    holdings: list[dict],
    map_path: Path = DEFAULT_MAP_PATH,
    cache_path: Path = fx.DEFAULT_CACHE_PATH,
) -> dict:
    """Refresh quotes for every holding in place. Returns a {status: count} summary."""
    mapping = load_map(map_path)
    mapping_changed = False
    summary = {"ok": 0, "unmapped": 0, "lookup_failed": 0}

    for holding in holdings:
        changed = refresh_holding(holding, mapping, cache_path)
        mapping_changed = mapping_changed or changed
        status = holding.get("quote_status", "lookup_failed")
        summary[status] = summary.get(status, 0) + 1

    if mapping_changed:
        save_map(mapping, map_path)

    return summary


def enrich_metadata(holdings: list[dict], map_path: Path = DEFAULT_MAP_PATH) -> None:
    """Apply curated per-ISIN `asset_class`/`asset_subclass`/`description` from the mapping
    file to every holding in place. Local lookup only, no network calls — safe to run on
    every CLI invocation (ingestion or quote-refresh) so metadata never goes stale relative
    to the curated config, regardless of which branch actually ran.
    """
    mapping = load_map(map_path)
    for holding in holdings:
        entry = mapping.get(holding.get("isin")) or {}
        holding["asset_class"] = entry.get("asset_class")
        holding["asset_subclass"] = entry.get("asset_subclass")
        holding["description"] = entry.get("description")
