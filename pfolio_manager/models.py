"""Normalized data shapes shared across parsers, fx/quote enrichment, and storage.

Each dataclass here maps 1:1 to what will eventually become a DB table row —
kept as plain dataclasses (no ORM) since the store is a single JSON file for now.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

SCHEMA_VERSION = 1


@dataclass
class ParsedLine:
    """One instrument row as read from a broker file, before FX/quote enrichment."""

    isin: Optional[str]
    instrument_name: str
    ticker: Optional[str]
    market: Optional[str]
    currency: str
    quantity: float
    avg_cost_price: Optional[float]
    cost_value: Optional[float]
    market_price: Optional[float]
    market_value: Optional[float]
    unrealized_gain_loss: Optional[float]
    unrealized_gain_loss_pct: Optional[float]
    accrued_interest: Optional[float]
    broker_fx_rate: Optional[float]  # raw rate as given by the broker, if any


@dataclass
class ParsedReport:
    """Output of a broker-specific parser: one report file, many instrument lines."""

    broker: str
    account_id: str
    as_of_date: str  # ISO date "YYYY-MM-DD"
    currency: str  # report base currency (EUR for all 3 brokers)
    total_value_native: Optional[float]  # broker-stated total, for sanity checks
    source_file: str
    lines: list[ParsedLine] = field(default_factory=list)


@dataclass
class Holding:
    """Latest known state of a single instrument, upserted on ingestion/quote refresh."""

    holding_id: str
    broker: str
    account_id: str
    isin: Optional[str]
    instrument_name: str
    ticker: Optional[str]
    market: Optional[str]
    currency: str
    quantity: float
    avg_cost_price: Optional[float]
    cost_value: Optional[float]
    market_price: Optional[float]
    market_value: Optional[float]
    market_value_eur: Optional[float]
    cost_value_eur: Optional[float]
    unrealized_gain_loss: Optional[float]
    unrealized_gain_loss_pct: Optional[float]
    accrued_interest: Optional[float]
    fx_rate_used: Optional[float]
    fx_rate_source: str  # identity | broker | frankfurter | stale_cache | unresolved
    as_of_date: str
    source_file: str
    parsed_at: str
    quote_last_refreshed_at: Optional[str] = None
    quote_as_of_date: Optional[str] = None  # trading day the quote reflects (util.market_as_of_date)
    quote_source: str = "broker_report"
    yfinance_ticker: Optional[str] = None
    quote_status: str = "ok"  # ok | unmapped | lookup_failed
    # Curated per-ISIN metadata (config/isin_ticker_map.json), applied by
    # quotes.enrich_metadata — not derived from the broker report itself.
    asset_class: Optional[str] = None  # Azionario | Obbligazionario | Bilanciato/Flessibile | Altro
    asset_subclass: Optional[str] = None  # finer-grained category, e.g. "Azionario USA"
    description: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Snapshot:
    """Append-only, denormalized copy of a broker/account's holdings as of one date."""

    snapshot_id: str
    broker: str
    account_id: str
    as_of_date: str
    total_value_native: Optional[float]
    total_value_eur: Optional[float]
    currency: str
    source_file: str
    parsed_at: str
    lines: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DailySnapshot:
    """Full copy of every holding for one trading day — one entry per calendar day,
    covering both report-ingestion days and quote-refresh-only days. Distinct from
    `Snapshot`, which is per broker/account and only created on report ingestion.
    """

    as_of_date: str  # trading day the quotes reflect (util.market_as_of_date)
    generated_at: str  # wall-clock timestamp this snapshot was actually written
    total_value_eur: Optional[float]
    holdings: list[dict] = field(default_factory=list)
    note: Optional[str] = None  # set when reconstructed after the fact rather than captured live

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class IngestedSource:
    """Record of a raw report file already processed, keyed by content hash."""

    file_hash: str
    file_name: str
    broker: str
    ingested_at: str
    as_of_date: str

    def to_dict(self) -> dict:
        return asdict(self)


def holding_natural_key(broker: str, account_id: str, isin: Optional[str], instrument_name: str) -> str:
    """Upsert key: ISIN when available, else fall back to the instrument name."""
    identity = isin if isin else instrument_name
    return f"{broker}:{account_id}:{identity}"
