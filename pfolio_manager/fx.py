"""EUR conversion: broker-supplied rates when usable, else Frankfurter.dev, with a local cache.

Resolution order per value: EUR is an identity conversion; a usable broker-supplied rate
is preferred over calling out to the API; Frankfurter is used as the general case (this is
always the path for Directa, whose export has no FX-rate column at all); if the API is
unreachable, the most recent cached rate for that currency pair is used as a fallback; if
nothing is resolvable, the value is left as None with an explicit "unresolved" flag rather
than silently guessing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import requests

FX_API_BASE = "https://api.frankfurter.dev/v1"
DEFAULT_CACHE_PATH = Path("data/fx_cache.json")
REQUEST_TIMEOUT = 10


def _load_cache(cache_path: Path) -> dict:
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    return {}


def _save_cache(cache_path: Path, cache: dict) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def _fetch_frankfurter_rate(currency: str, as_of_date: str) -> Optional[float]:
    """Return EUR per 1 unit of `currency` on `as_of_date` ("YYYY-MM-DD"), or None on failure."""
    url = f"{FX_API_BASE}/{as_of_date}"
    try:
        resp = requests.get(url, params={"base": currency, "symbols": "EUR"}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()["rates"]["EUR"]
    except (requests.RequestException, KeyError, ValueError, TypeError):
        return None


def get_eur_rate(
    currency: str, as_of_date: str, cache_path: Path = DEFAULT_CACHE_PATH
) -> tuple[Optional[float], str]:
    """Return (eur_per_unit_rate, source), source in {"frankfurter", "stale_cache", "unresolved"}."""
    pair_key = f"{currency}_EUR"
    cache = _load_cache(cache_path)
    pair_cache = cache.get(pair_key, {})

    if as_of_date in pair_cache:
        return pair_cache[as_of_date], "frankfurter"

    rate = _fetch_frankfurter_rate(currency, as_of_date)
    if rate is not None:
        pair_cache[as_of_date] = rate
        cache[pair_key] = pair_cache
        _save_cache(cache_path, cache)
        return rate, "frankfurter"

    if pair_cache:
        latest_date = max(pair_cache)
        return pair_cache[latest_date], "stale_cache"

    return None, "unresolved"


def resolve_eur_value(
    value_native: Optional[float],
    currency: str,
    as_of_date: str,
    broker_fx_rate: Optional[float] = None,
    cache_path: Path = DEFAULT_CACHE_PATH,
) -> tuple[Optional[float], Optional[float], str]:
    """Resolve a native-currency value to EUR.

    Returns (value_eur, fx_rate_used, fx_rate_source). `fx_rate_used` is always expressed
    as EUR per 1 unit of `currency`, i.e. value_eur = value_native * fx_rate_used.
    """
    if value_native is None:
        return None, None, "unresolved"

    if currency == "EUR":
        return value_native, 1.0, "identity"

    if broker_fx_rate:
        # Assumed convention: broker rate = units of `currency` per 1 EUR (the indirect
        # quotation style seen on Italian broker platforms) -> invert to get EUR-per-unit.
        # Not yet empirically validated against a real non-EUR broker-rate line (none of
        # the sample files had one) — cross-check against the broker's own stated total
        # the first time this path is actually exercised, per the plan's verification step.
        eur_rate = 1.0 / broker_fx_rate
        return value_native * eur_rate, eur_rate, "broker"

    eur_rate, source = get_eur_rate(currency, as_of_date, cache_path)
    if eur_rate is None:
        return None, None, "unresolved"
    return value_native * eur_rate, eur_rate, source
