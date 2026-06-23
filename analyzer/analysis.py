"""Scoring + analysis engine.

Turns raw data into an auditable verdict. Every sub-score is rule-based and
returns the human-readable reasons behind it, so you can always see *why* a
stock scored the way it did rather than trusting a black box.

Composite score is 0-100, a weighted blend of five pillars:
    Valuation, Growth, Profitability, Financial health, and Smart-money/Momentum.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from . import data


@dataclass
class Pillar:
    name: str
    score: float  # 0-100
    weight: float
    reasons: list[str] = field(default_factory=list)  # positives
    flags: list[str] = field(default_factory=list)  # negatives


@dataclass
class Analysis:
    ticker: str
    name: str
    price: float | None
    sector: str
    composite: float
    verdict: str
    pillars: list[Pillar]
    upside_pct: float | None  # to analyst mean target (local/yfinance only)
    analyst_rating: str | None  # Strong Buy / Buy / Hold / Sell / Strong Sell
    analyst_bullish_pct: float | None  # % of analysts rating Buy or Strong Buy
    info: dict[str, Any]

    @property
    def all_reasons(self) -> list[str]:
        out = []
        for p in self.pillars:
            out += p.reasons
        return out

    @property
    def all_flags(self) -> list[str]:
        out = []
        for p in self.pillars:
            out += p.flags
        return out


def _clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _g(info: dict, *keys, default=None):
    """First non-None value among keys."""
    for k in keys:
        v = info.get(k)
        if v is not None:
            return v
    return default


# --------------------------------------------------------------------------- #
# Individual pillars
# --------------------------------------------------------------------------- #
# Finnhub/Yahoo lump crypto miners, quantum, and AI-infra names under a generic
# "Technology" industry. These overrides give a more honest sub-sector label.
# Keyed by ticker; falls back to the data provider's industry when not listed.
_SECTOR_OVERRIDES = {
    # Bitcoin / crypto miners
    "RIOT": "Crypto Mining", "MARA": "Crypto Mining", "CLSK": "Crypto Mining",
    "IREN": "Crypto Mining", "CIFR": "Crypto Mining", "BITF": "Crypto Mining",
    "HUT": "Crypto Mining", "WULF": "Crypto Mining", "BTBT": "Crypto Mining",
    "HIVE": "Crypto Mining", "CORZ": "Crypto Mining", "GREE": "Crypto Mining",
    "BTDR": "Crypto Mining", "SDIG": "Crypto Mining",
    # AI / HPC data-center infrastructure (often crypto-adjacent)
    "APLD": "AI/Crypto Infrastructure", "CRWV": "AI Cloud Infrastructure",
    "NBIS": "AI Cloud Infrastructure",
    # Quantum computing
    "QUBT": "Quantum Computing", "RGTI": "Quantum Computing",
    "IONQ": "Quantum Computing", "QBTS": "Quantum Computing", "ARQQ": "Quantum Computing",
    # Crypto exchanges / treasuries
    "COIN": "Crypto Exchange", "MSTR": "Bitcoin Treasury", "HOOD": "Fintech",
}


def refine_sector(ticker: str, raw_sector: str | None) -> str:
    """Return a precise sub-sector label, overriding generic provider buckets."""
    override = _SECTOR_OVERRIDES.get(ticker.upper())
    if override:
        return override
    return raw_sector or "—"


def _valuation(info: dict) -> Pillar:
    p = Pillar("Valuation", 50, 0.20)
    pe = _g(info, "trailingPE", "forwardPE")
    peg = info.get("pegRatio")
    ps = info.get("priceToSalesTrailing12Months")
    pb = info.get("priceToBook")

    if pe is not None:
        if pe <= 0:
            p.flags.append("Negative earnings (no positive P/E)")
            p.score -= 10
        elif pe < 15:
            p.reasons.append(f"Low P/E of {pe:.1f} — cheap vs. the market")
            p.score += 20
        elif pe < 25:
            p.reasons.append(f"Reasonable P/E of {pe:.1f}")
            p.score += 8
        elif pe > 40:
            p.flags.append(f"Rich P/E of {pe:.1f} — priced for high growth")
            p.score -= 15
    if peg is not None and peg > 0:
        if peg < 1:
            p.reasons.append(f"PEG {peg:.2f} (<1) — growth is cheap relative to price")
            p.score += 15
        elif peg > 2.5:
            p.flags.append(f"PEG {peg:.2f} — expensive even after growth")
            p.score -= 10
    if ps is not None and ps > 20:
        p.flags.append(f"Very high P/S of {ps:.1f}")
        p.score -= 8
    if pb is not None and 0 < pb < 1.5:
        p.reasons.append(f"Low price-to-book of {pb:.2f}")
        p.score += 5
    p.score = _clamp(p.score)
    return p


def _growth(info: dict) -> Pillar:
    p = Pillar("Growth", 50, 0.22)
    rev = info.get("revenueGrowth")
    earn = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
    if rev is not None:
        pct = rev * 100
        if pct > 30:
            p.reasons.append(f"Revenue growing {pct:.0f}% YoY — exceptional")
            p.score += 28
        elif pct > 15:
            p.reasons.append(f"Revenue growing {pct:.0f}% YoY — strong")
            p.score += 16
        elif pct > 5:
            p.reasons.append(f"Revenue growing {pct:.0f}% YoY")
            p.score += 6
        elif pct < 0:
            p.flags.append(f"Revenue shrinking {pct:.0f}% YoY")
            p.score -= 20
    if earn is not None:
        pct = earn * 100
        if pct > 25:
            p.reasons.append(f"Earnings growing {pct:.0f}% YoY")
            p.score += 16
        elif pct < -10:
            p.flags.append(f"Earnings falling {pct:.0f}% YoY")
            p.score -= 14
    p.score = _clamp(p.score)
    return p


def _profitability(info: dict) -> Pillar:
    p = Pillar("Profitability", 50, 0.18)
    margin = info.get("profitMargins")
    roe = info.get("returnOnEquity")
    if margin is not None:
        pct = margin * 100
        if pct > 20:
            p.reasons.append(f"Fat net margin of {pct:.0f}%")
            p.score += 20
        elif pct > 8:
            p.reasons.append(f"Solid net margin of {pct:.0f}%")
            p.score += 8
        elif pct < 0:
            p.flags.append(f"Unprofitable ({pct:.0f}% net margin)")
            p.score -= 20
    if roe is not None:
        pct = roe * 100
        if pct > 20:
            p.reasons.append(f"High return on equity ({pct:.0f}%)")
            p.score += 16
        elif pct < 0:
            p.flags.append(f"Negative return on equity ({pct:.0f}%)")
            p.score -= 10
    p.score = _clamp(p.score)
    return p


def _health(info: dict) -> Pillar:
    p = Pillar("Financial health", 50, 0.18)
    de = info.get("debtToEquity")
    cr = info.get("currentRatio")
    fcf = info.get("freeCashflow")
    if de is not None:
        if de < 50:
            p.reasons.append(f"Low debt/equity of {de:.0f}")
            p.score += 16
        elif de > 200:
            p.flags.append(f"High leverage (debt/equity {de:.0f})")
            p.score -= 18
    if cr is not None:
        if cr > 1.5:
            p.reasons.append(f"Healthy current ratio of {cr:.1f}")
            p.score += 10
        elif cr < 1:
            p.flags.append(f"Current ratio below 1 ({cr:.1f}) — liquidity risk")
            p.score -= 12
    if fcf is not None:
        if fcf > 0:
            p.reasons.append("Positive free cash flow")
            p.score += 12
        else:
            p.flags.append("Burning cash (negative free cash flow)")
            p.score -= 14
    p.score = _clamp(p.score)
    return p


def _smart_money(ticker: str, info: dict, hist: pd.DataFrame, consensus: dict | None = None) -> Pillar:
    """Combines what big/insider money is doing with analyst views and momentum."""
    p = Pillar("Smart money & momentum", 50, 0.22)

    # Analyst recommendation consensus (Finnhub free endpoint).
    if consensus and consensus.get("rating"):
        rating = consensus["rating"]
        pct = consensus.get("bullish_pct")
        suffix = f" ({pct:.0f}% bullish)" if pct is not None else ""
        if rating in ("Strong Buy", "Buy"):
            p.reasons.append(f"Analysts rate it {rating}{suffix}")
            p.score += 10 if rating == "Strong Buy" else 7
        elif rating in ("Sell", "Strong Sell"):
            p.flags.append(f"Analysts rate it {rating}{suffix}")
            p.score -= 10 if rating == "Strong Sell" else 7

    # Insider net buying (Form 4) — net buys are a strong conviction signal.
    insiders = data.get_insider_transactions(ticker)
    if isinstance(insiders, pd.DataFrame) and not insiders.empty:
        txt_col = next((c for c in insiders.columns if "Transaction" in c or "Text" in c), None)
        if txt_col:
            recent = insiders.head(20)[txt_col].astype(str).str.lower()
            buys = recent.str.contains("buy|purchase").sum()
            sells = recent.str.contains("sale|sell").sum()
            if buys > sells and buys > 0:
                p.reasons.append(f"Insiders net buyers recently ({buys} buys vs {sells} sells)")
                p.score += 14
            elif sells > buys * 2 and sells > 2:
                p.flags.append(f"Heavy insider selling ({sells} sells vs {buys} buys)")
                p.score -= 8

    # Institutional ownership level.
    inst = info.get("heldPercentInstitutions")
    if inst is not None and inst > 0.6:
        p.reasons.append(f"Institutions hold {inst*100:.0f}% — strong professional backing")
        p.score += 6

    # Buyback: companies reducing share count are returning cash + signaling confidence.
    cf = data.get_cashflow(ticker)
    if isinstance(cf, pd.DataFrame) and not cf.empty:
        repurchase_rows = [r for r in cf.index if "repurchase" in str(r).lower()]
        if repurchase_rows:
            val = cf.loc[repurchase_rows[0]].iloc[0]
            if pd.notna(val) and val < 0:  # outflow = buying back stock
                p.reasons.append("Company is buying back its own stock")
                p.score += 8

    # Price momentum. Prefer a real series (200-day MA + 6-month return) when we
    # have one; otherwise fall back to Finnhub's precomputed 6-month return so the
    # signal still works on cloud where Yahoo price history is unavailable.
    if isinstance(hist, pd.DataFrame) and len(hist) > 60:
        close = hist["Close"].dropna()
        last = close.iloc[-1]
        ma200 = close.rolling(min(200, len(close))).mean().iloc[-1]
        if last > ma200:
            p.reasons.append("Trading above its 200-day average (uptrend)")
            p.score += 8
        else:
            p.flags.append("Below its 200-day average (downtrend)")
            p.score -= 8
        lookback = close.iloc[-min(126, len(close))]
        ret6 = (last / lookback - 1) * 100
    else:
        ret6 = info.get("_mom6m")

    if ret6 is not None:
        if ret6 > 25:
            p.reasons.append(f"Up {ret6:.0f}% over ~6 months (strong momentum)")
            p.score += 8
        elif ret6 < -25:
            p.flags.append(f"Down {ret6:.0f}% over ~6 months (weak momentum)")
            p.score -= 8

    p.score = _clamp(p.score)
    return p


# --------------------------------------------------------------------------- #
# Top-level
# --------------------------------------------------------------------------- #
def analyze(ticker: str) -> Analysis:
    """Full analysis for one ticker."""
    ticker = ticker.strip().upper()
    info = data.get_info(ticker)
    hist = data.get_price_history(ticker, "1y")
    consensus = data.get_analyst_consensus(ticker)

    pillars = [
        _valuation(info),
        _growth(info),
        _profitability(info),
        _health(info),
        _smart_money(ticker, info, hist, consensus),
    ]
    total_w = sum(p.weight for p in pillars)
    composite = sum(p.score * p.weight for p in pillars) / total_w if total_w else 50.0

    price = _g(info, "currentPrice", "regularMarketPrice")
    target = info.get("targetMeanPrice")
    upside = ((target / price - 1) * 100) if (target and price) else None

    return Analysis(
        ticker=ticker,
        name=info.get("longName") or info.get("shortName") or ticker,
        price=price,
        sector=refine_sector(ticker, info.get("sector")),
        composite=round(composite, 1),
        verdict=_verdict(composite),
        pillars=pillars,
        upside_pct=round(upside, 1) if upside is not None else None,
        analyst_rating=consensus.get("rating") if consensus else None,
        analyst_bullish_pct=consensus.get("bullish_pct") if consensus else None,
        info=info,
    )


def _verdict(score: float) -> str:
    if score >= 75:
        return "Strong"
    if score >= 60:
        return "Good"
    if score >= 45:
        return "Mixed"
    if score >= 30:
        return "Weak"
    return "Avoid"


# --------------------------------------------------------------------------- #
# Opportunity screener: "stocks that could produce incredible returns"
# --------------------------------------------------------------------------- #
def opportunity_score(a: Analysis) -> float:
    """Re-weight an analysis toward asymmetric upside potential.

    High-return candidates pair strong growth + smart-money conviction with
    valuation that isn't already stretched, and meaningful analyst upside.
    This is deliberately growth-tilted vs. the balanced composite.
    """
    by_name = {p.name: p.score for p in a.pillars}
    base = (
        0.40 * by_name.get("Growth", 50)
        + 0.25 * by_name.get("Smart money & momentum", 50)
        + 0.20 * by_name.get("Valuation", 50)
        + 0.15 * by_name.get("Profitability", 50)
    )
    if a.upside_pct is not None:
        base += min(20, max(-10, a.upside_pct / 3))  # analyst dollar-upside bonus, capped
    elif a.analyst_bullish_pct is not None:
        base += (a.analyst_bullish_pct - 50) / 5  # consensus bonus: -10..+10 around neutral
    return round(_clamp(base), 1)


def screen(tickers: list[str]) -> pd.DataFrame:
    """Rank a universe of tickers by opportunity score. Returns a tidy DataFrame."""
    rows = []
    for t in tickers:
        try:
            a = analyze(t)
        except Exception:
            continue
        rows.append(
            {
                "Ticker": a.ticker,
                "Name": a.name[:32],
                "Price": a.price,
                "Opportunity": opportunity_score(a),
                "Composite": a.composite,
                "Verdict": a.verdict,
                "Analyst": a.analyst_rating or "—",
                "Sector": a.sector,
                "Top reason": (a.all_reasons[0] if a.all_reasons else "—"),
                "Top risk": (a.all_flags[0] if a.all_flags else "—"),
            }
        )
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values("Opportunity", ascending=False).reset_index(drop=True)
    return df
