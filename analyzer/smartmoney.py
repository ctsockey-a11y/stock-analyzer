"""Cross-reference tickers with congressional trades and big-investor 13F holdings.

Produces ticker-keyed signals so the rest of the app can flag holdings, rank the
screener, and build "what smart money bought" universes — all from the free
sources in data.py (House/Senate disclosures + SEC 13F).
"""
from __future__ import annotations

import re

import requests

from . import data

# Corporate-name noise to strip before matching a 13F issuer name to a ticker.
_SUFFIXES = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
    "PLC", "LP", "LLC", "HOLDINGS", "HOLDING", "GROUP", "THE", "COM", "CL", "CLASS",
    "A", "B", "C", "SA", "NV", "AG", "TR", "TRUST", "FUND", "ETF", "COMMON", "STOCK",
    "NEW", "DEL", "DE", "REIT", "INTL", "INTERNATIONAL",
}


def _norm(name: str) -> str:
    """Normalize a company name to a comparable key (drop punctuation + suffixes)."""
    name = re.sub(r"[^A-Za-z0-9 ]", " ", (name or "").upper())
    toks = [t for t in name.split() if t not in _SUFFIXES]
    return " ".join(toks).strip()


_NAME2TICK: dict[str, str] | None = None


def _name_to_ticker() -> dict[str, str]:
    """Build {normalized company name -> ticker} from SEC's public mapping file."""
    global _NAME2TICK
    if _NAME2TICK is None:
        _NAME2TICK = {}
        try:
            r = requests.get("https://www.sec.gov/files/company_tickers.json", headers=data.SEC_HEADERS, timeout=15)
            r.raise_for_status()
            for row in r.json().values():
                key = _norm(row["title"])
                tk = row["ticker"].upper()
                # On collisions prefer the shorter ticker (usually the common share).
                if key and (key not in _NAME2TICK or len(tk) < len(_NAME2TICK[key])):
                    _NAME2TICK[key] = tk
        except Exception:
            _NAME2TICK = {}
    return _NAME2TICK


def congress_activity(house_reports: int = 25, senate_reports: int = 20) -> dict[str, dict]:
    """Aggregate recent House + Senate trades by ticker."""
    trades = data.get_congress_trades(house_reports) + data.get_senate_trades(senate_reports)
    agg: dict[str, dict] = {}
    for t in trades:
        a = agg.setdefault(t["ticker"], {"buys": 0, "sells": 0, "actors": []})
        if "Buy" in t["type"]:
            a["buys"] += 1
        elif "Sell" in t["type"]:
            a["sells"] += 1
        if t["member"] not in a["actors"]:
            a["actors"].append(t["member"])
    return agg


def fund_activity() -> dict[str, list[str]]:
    """Map ticker -> list of famous funds holding it (from their latest 13F)."""
    n2t = _name_to_ticker()
    agg: dict[str, list[str]] = {}
    for fund, cik in data.FAMOUS_FUNDS.items():
        short = fund.split("—")[-1].strip() if "—" in fund else fund
        for h in data.get_13f_holdings(cik, top=15).get("holdings", []):
            tk = n2t.get(_norm(h["issuer"]))
            if tk and short not in agg.get(tk, []):
                agg.setdefault(tk, []).append(short)
    return agg


def flag(ticker: str, congress: dict, funds: dict) -> str:
    """Compact smart-money flag string for a ticker, e.g. '🏛️2B 🏦Buffett'."""
    tk = ticker.upper()
    parts = []
    c = congress.get(tk)
    if c:
        if c["buys"]:
            parts.append(f"🏛️{c['buys']}B")
        if c["sells"]:
            parts.append(f"🏛️{c['sells']}S")
    f = funds.get(tk)
    if f:
        parts.append("🏦" + ("/".join(f[:2]) + ("…" if len(f) > 2 else "")))
    return " ".join(parts) or "—"


def score_bonus(ticker: str, congress: dict, funds: dict) -> float:
    """Opportunity-score nudge from smart-money: buying/holding adds, selling subtracts."""
    tk = ticker.upper()
    bonus = 0.0
    c = congress.get(tk)
    if c:
        bonus += min(10, c["buys"] * 4) - min(6, c["sells"] * 2)
    f = funds.get(tk)
    if f:
        bonus += min(12, len(f) * 5)
    return bonus
