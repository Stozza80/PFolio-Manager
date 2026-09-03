"""Price scraping fallback for instruments not available on Yahoo Finance.

Two Borsa Italiana sources, both static HTML (no JS execution needed) and confirmed
not blocked by robots.txt:
- MOT "euro-obbligazioni" bond pages (`fetch_mot_bond_quote`), ISIN-parameterized —
  works for Italian and foreign government/corporate bonds admitted to that segment.
  Prices are quoted per 100 nominal, per MOT convention — the caller must divide
  accordingly when computing a market value (see quotes.py).
- Fund detail pages (`fetch_borsaitaliana_fund_quote`), keyed by Borsa Italiana's own
  internal fund code rather than ISIN (these pages aren't ISIN-parameterized) — the
  code has to be found once per instrument (e.g. from the page URL) and curated.

A third source, stockevents.app (`fetch_stockevents_quote`), is ISIN-parameterized
(`/stock/{isin}.FUND`) and embeds the price in a static schema.org FAQPage JSON-LD block
rather than an HTML table — parsed via a regex over the FAQ answer text. robots.txt has
no restrictions on this path. Found to work for funds not present on Yahoo Finance or
Borsa Italiana's own fund pages.

Other sources tried during development and found impractical: Bloomberg (blocks
scripted requests with HTTP 403, and its robots.txt explicitly carves out restrictions
for AI-bot user agents elsewhere on the site) and borse.it (HTTP 402 Payment Required —
paywalled). Instruments only found on those sites currently have no automated quote
source in this project and are flagged `unmapped`/`lookup_failed` rather than silently
skipped.
"""
from __future__ import annotations

import json
import re

from typing import Optional

import requests
from bs4 import BeautifulSoup

MOT_BOND_URL = (
    "https://www.borsaitaliana.it/borsa/obbligazioni/mot/euro-obbligazioni/dati-completi.html"
)
PRICE_LABEL = "Prezzo Ultimo Contratto"
CURRENCY_LABEL = "Valuta di negoziazione"
REQUEST_TIMEOUT = 15
USER_AGENT = "Mozilla/5.0 (compatible; PFolio-Manager/1.0; personal portfolio tracker)"


def _labelled_value(soup: BeautifulSoup, label_text: str) -> Optional[str]:
    """Find a `<strong>label</strong>` cell and return the text of its sibling `<td>`.

    The MOT page lays out each field as a two-cell table row: `<td><strong>Label</strong></td>
    <td>Value</td>`. This is more resilient to markup/CSS-class redesigns than matching on
    a styling class name.
    """
    label = soup.find("strong", string=label_text)
    if label is None:
        return None
    label_cell = label.find_parent("td")
    if label_cell is None:
        return None
    value_cell = label_cell.find_next_sibling("td")
    if value_cell is None:
        return None
    return value_cell.get_text(strip=True)


def fetch_mot_bond_quote(isin: str) -> tuple[Optional[float], Optional[str]]:
    """Return (last_contract_price, trading_currency) for a bond on Borsa Italiana's MOT
    euro-obbligazioni segment, or (None, None) if not found/not listed there.

    The price is quoted per 100 nominal (MOT convention) — the caller must divide
    accordingly when computing a market value (see quotes.py). The trading currency is
    NOT always EUR despite the URL's "euro-obbligazioni" segment name: foreign bonds
    admitted to this segment can trade in their own currency (confirmed with a USD-
    denominated Polish government bond).
    """
    try:
        resp = requests.get(
            MOT_BOND_URL,
            params={"isin": isin, "mic": "MOTX", "lang": "it"},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    price_text = _labelled_value(soup, PRICE_LABEL)
    currency = _labelled_value(soup, CURRENCY_LABEL)
    if price_text is None:
        return None, None

    try:
        price = float(price_text.replace(".", "").replace(",", "."))
    except ValueError:
        return None, None

    return price, currency


FUND_DETAIL_URL = "https://www.borsaitaliana.it/borsa/fondi/dettaglio/{code}.html"
FUND_PRICE_COLUMN = "Ultima"
FUND_CURRENCY_COLUMN = "Valuta"


def fetch_borsaitaliana_fund_quote(fund_code: str) -> tuple[Optional[float], Optional[str]]:
    """Return (last_nav, currency) for a fund on Borsa Italiana's fund detail page, or
    (None, None) if not found.

    `fund_code` is Borsa Italiana's own internal fund code (e.g. "2FADB161685"), NOT the
    ISIN — unlike the MOT bond page, these pages aren't ISIN-parameterized, so the code
    must be found once (from the page URL) and curated per instrument.
    """
    url = FUND_DETAIL_URL.format(code=fund_code)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None, None

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="m-table")
    if table is None:
        return None, None
    header_labels = [th.get_text(strip=True) for th in table.find_all("th")]
    body = table.find("tbody")
    first_row = body.find("tr") if body else None
    if first_row is None:
        return None, None
    row_values = [td.get_text(strip=True) for td in first_row.find_all("td")]
    if len(row_values) != len(header_labels):
        return None, None

    values_by_label = dict(zip(header_labels, row_values))
    price_text = values_by_label.get(FUND_PRICE_COLUMN)
    currency = values_by_label.get(FUND_CURRENCY_COLUMN)
    if not price_text:
        return None, None
    try:
        price = float(price_text.replace(".", "").replace(",", "."))
    except ValueError:
        return None, None

    return price, currency


STOCKEVENTS_URL = "https://stockevents.app/it/stock/{isin}.FUND"
_STOCKEVENTS_PRICE_RE = re.compile(r"prezzo attuale di [^\"]*? è €\s*([\d.,]+)\s*([A-Z]{3})")


def fetch_stockevents_quote(isin: str) -> tuple[Optional[float], Optional[str]]:
    """Return (last_price, currency) for a fund/security on stockevents.app, or
    (None, None) if not found. ISIN-parameterized, unlike the Borsa Italiana fund pages.

    The price isn't in an HTML table — it's embedded in a static schema.org FAQPage
    JSON-LD block (an SEO FAQ answer), extracted here with a regex over that text rather
    than full JSON parsing, since the answer wording is itself the only stable contract.
    """
    url = STOCKEVENTS_URL.format(isin=isin)
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None, None

    match = _STOCKEVENTS_PRICE_RE.search(resp.text)
    if match is None:
        return None, None

    price_text, currency = match.groups()
    try:
        price = float(price_text.replace(".", "").replace(",", "."))
    except ValueError:
        return None, None

    return price, currency
