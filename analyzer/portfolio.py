"""Holdings parsing and portfolio-level analysis."""
from __future__ import annotations

import io

import pandas as pd

from . import analysis
from .analysis import Analysis


def parse_holdings(raw: str | bytes) -> pd.DataFrame:
    """Parse a holdings CSV (ticker,shares[,cost_basis]) into a clean DataFrame.

    Tolerant of header casing and extra columns. Returns columns:
    ticker, shares, cost_basis (cost_basis may be NaN).
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="ignore")
    df = pd.read_csv(io.StringIO(raw))
    df.columns = [c.strip().lower() for c in df.columns]
    if "ticker" not in df.columns:
        # Assume first column is the ticker if unlabeled.
        df = df.rename(columns={df.columns[0]: "ticker"})
    if "shares" not in df.columns:
        df["shares"] = 0.0
    if "cost_basis" not in df.columns:
        df["cost_basis"] = pd.NA
    df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
    df["shares"] = pd.to_numeric(df["shares"], errors="coerce").fillna(0.0)
    df["cost_basis"] = pd.to_numeric(df["cost_basis"], errors="coerce")
    df = df[df["ticker"].str.len() > 0][["ticker", "shares", "cost_basis"]]
    return df.reset_index(drop=True)


def analyze_portfolio(holdings: pd.DataFrame) -> tuple[pd.DataFrame, dict, list[Analysis]]:
    """Analyze each holding and roll up portfolio totals.

    Returns (per_position_df, summary_dict, list_of_Analysis).
    """
    rows = []
    analyses: list[Analysis] = []
    for _, h in holdings.iterrows():
        a = analysis.analyze(h["ticker"])
        analyses.append(a)
        price = a.price or 0.0
        shares = float(h["shares"])
        value = price * shares
        cost = float(h["cost_basis"]) if pd.notna(h["cost_basis"]) else None
        gain_pct = ((price / cost - 1) * 100) if (cost and cost > 0) else None
        rows.append(
            {
                "Ticker": a.ticker,
                "Name": a.name[:28],
                "Shares": shares,
                "Price": price,
                "Value": value,
                "Gain %": round(gain_pct, 1) if gain_pct is not None else None,
                "Health": a.composite,
                "Verdict": a.verdict,
                "Upside %": a.upside_pct,
                "Sector": a.sector,
            }
        )
    df = pd.DataFrame(rows)

    summary: dict = {}
    if not df.empty:
        total = df["Value"].sum()
        df["Weight %"] = (df["Value"] / total * 100).round(1) if total else 0.0
        weighted_health = (df["Health"] * df["Value"]).sum() / total if total else df["Health"].mean()
        summary = {
            "total_value": total,
            "positions": len(df),
            "weighted_health": round(weighted_health, 1),
            "best": df.loc[df["Health"].idxmax(), "Ticker"] if total else None,
            "worst": df.loc[df["Health"].idxmin(), "Ticker"] if total else None,
            "sector_weights": df.groupby("Sector")["Value"].sum().sort_values(ascending=False).to_dict(),
            "concentration": round(df["Weight %"].max(), 1) if total else 0.0,
        }
        df = df.sort_values("Value", ascending=False).reset_index(drop=True)
    return df, summary, analyses


def portfolio_warnings(summary: dict) -> list[str]:
    """Plain-language risk warnings about the portfolio as a whole."""
    out = []
    if not summary:
        return out
    if summary.get("concentration", 0) > 35:
        out.append(f"⚠️ Concentrated: your largest position is {summary['concentration']:.0f}% of the portfolio.")
    weights = summary.get("sector_weights", {})
    total = sum(weights.values()) or 1
    for sector, val in weights.items():
        share = val / total * 100
        if share > 50 and sector != "—":
            out.append(f"⚠️ {share:.0f}% of your money is in one sector ({sector}).")
    if summary.get("weighted_health", 100) < 45:
        out.append("⚠️ Portfolio's weighted health score is low — many holdings score poorly.")
    return out
