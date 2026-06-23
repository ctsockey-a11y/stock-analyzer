"""Data fetching layer.

All external data comes from free, key-less sources by default:
  * yfinance  -> prices, fundamentals, institutional holders, insider trades, news
  * SEC EDGAR -> latest regulatory filings (no key, just a User-Agent)
An optional free Finnhub key enriches news with sentiment.

Every fetch is defensive: market data is messy and endpoints change, so each
function returns a safe empty value rather than raising, and the UI degrades
gracefully when a piece is missing.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

import pandas as pd
import requests
import yfinance as yf

# SEC requires a descriptive User-Agent with contact info per their fair-access policy.
SEC_HEADERS = {"User-Agent": "stock-analyzer (personal research; contact: ctsockey@gmail.com)"}
_HTTP_TIMEOUT = 15


# --------------------------------------------------------------------------- #
# Core ticker data
# --------------------------------------------------------------------------- #
def get_info(ticker: str) -> dict[str, Any]:
    """Fundamental + descriptive fields for a ticker (yfinance .info)."""
    try:
        info = yf.Ticker(ticker).info or {}
        # yfinance sometimes returns a near-empty dict for bad/delisted tickers.
        if not info.get("regularMarketPrice") and not info.get("currentPrice"):
            # Still may have data under longName; keep it but flag price absence.
            pass
        return info
    except Exception:
        return {}


def get_price_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Daily OHLCV history. Empty DataFrame on failure."""
    try:
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=True)
        return hist if isinstance(hist, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def get_cashflow(ticker: str) -> pd.DataFrame:
    """Annual cash-flow statement (used to detect buybacks)."""
    try:
        cf = yf.Ticker(ticker).cashflow
        return cf if isinstance(cf, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def get_institutional_holders(ticker: str) -> pd.DataFrame:
    """Top institutional holders (the 'big money')."""
    try:
        df = yf.Ticker(ticker).institutional_holders
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def get_insider_transactions(ticker: str) -> pd.DataFrame:
    """Recent insider (officers/directors) buys and sells, from Form 4 data."""
    try:
        df = yf.Ticker(ticker).insider_transactions
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def get_recommendations(ticker: str) -> pd.DataFrame:
    """Analyst recommendation trend."""
    try:
        df = yf.Ticker(ticker).recommendations
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


# --------------------------------------------------------------------------- #
# News
# --------------------------------------------------------------------------- #
def get_news(ticker: str, finnhub_key: str | None = None, limit: int = 12) -> list[dict[str, Any]]:
    """Recent company news.

    Prefers Finnhub (adds per-article sentiment-friendly metadata) when a key is
    provided; otherwise falls back to free Yahoo Finance headlines via yfinance.
    Returns a normalized list of {title, publisher, url, datetime}.
    """
    if finnhub_key:
        items = _finnhub_news(ticker, finnhub_key, limit)
        if items:
            return items
    return _yahoo_news(ticker, limit)


def _finnhub_news(ticker: str, key: str, limit: int) -> list[dict[str, Any]]:
    try:
        today = _dt.date.today()
        frm = today - _dt.timedelta(days=14)
        r = requests.get(
            "https://finnhub.io/api/v1/company-news",
            params={"symbol": ticker, "from": frm.isoformat(), "to": today.isoformat(), "token": key},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        out = []
        for a in r.json()[:limit]:
            out.append(
                {
                    "title": a.get("headline", ""),
                    "publisher": a.get("source", ""),
                    "url": a.get("url", ""),
                    "datetime": _dt.datetime.fromtimestamp(a.get("datetime", 0)),
                    "summary": a.get("summary", ""),
                }
            )
        return out
    except Exception:
        return []


def _yahoo_news(ticker: str, limit: int) -> list[dict[str, Any]]:
    try:
        raw = yf.Ticker(ticker).news or []
        out = []
        for a in raw[:limit]:
            # yfinance news schema has shifted over versions; handle both shapes.
            content = a.get("content", a)
            title = content.get("title") or a.get("title", "")
            prov = content.get("provider", {})
            publisher = prov.get("displayName") if isinstance(prov, dict) else a.get("publisher", "")
            url = ""
            if isinstance(content.get("clickThroughUrl"), dict):
                url = content["clickThroughUrl"].get("url", "")
            url = url or content.get("canonicalUrl", {}).get("url", "") if isinstance(content.get("canonicalUrl"), dict) else url
            url = url or a.get("link", "")
            ts = a.get("providerPublishTime")
            when = _dt.datetime.fromtimestamp(ts) if ts else None
            out.append({"title": title, "publisher": publisher or "Yahoo Finance", "url": url, "datetime": when, "summary": ""})
        return out
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# SEC EDGAR
# --------------------------------------------------------------------------- #
def get_sec_filings(ticker: str, limit: int = 15) -> list[dict[str, Any]]:
    """Most recent SEC filings for a ticker via the free EDGAR submissions API.

    Highlights the form types that matter for this app:
      4      -> insider buy/sell
      13F-HR -> what institutions hold
      8-K    -> material events (often buyback announcements, M&A)
      10-K / 10-Q -> financial reports
    """
    cik = _ticker_to_cik(ticker)
    if not cik:
        return []
    try:
        r = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik}.json",
            headers=SEC_HEADERS,
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        recent = r.json().get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accession = recent.get("accessionNumber", [])
        docs = recent.get("primaryDocument", [])
        out = []
        for i in range(min(len(forms), len(dates))):
            acc = accession[i].replace("-", "") if i < len(accession) else ""
            doc = docs[i] if i < len(docs) else ""
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}/{doc}" if acc else ""
            out.append({"form": forms[i], "date": dates[i], "url": url})
        return out[:limit]
    except Exception:
        return []


_CIK_CACHE: dict[str, str] | None = None


def _ticker_to_cik(ticker: str) -> str | None:
    """Map a ticker to its zero-padded 10-digit CIK using SEC's public mapping file."""
    global _CIK_CACHE
    if _CIK_CACHE is None:
        try:
            r = requests.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=SEC_HEADERS,
                timeout=_HTTP_TIMEOUT,
            )
            r.raise_for_status()
            _CIK_CACHE = {row["ticker"].upper(): str(row["cik_str"]).zfill(10) for row in r.json().values()}
        except Exception:
            _CIK_CACHE = {}
    return _CIK_CACHE.get(ticker.upper())
