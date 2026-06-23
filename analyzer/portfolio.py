"""Holdings parsing and portfolio-level analysis."""
from __future__ import annotations

import io
import re

import pandas as pd

from . import analysis
from .analysis import Analysis

# Column-name aliases across common broker exports (Fidelity, Schwab, Vanguard, etc.).
_COLUMN_ALIASES = {
    "ticker": ["ticker", "symbol", "sym"],
    "shares": ["shares", "quantity", "qty", "share quantity", "shares owned"],
    "cost_basis": ["cost_basis", "average cost basis", "cost basis", "cost/share", "avg cost", "average cost"],
}
# A real equity/ETF ticker: 1-5 letters, optional .CLASS suffix (e.g. BRK.B).
_VALID_TICKER = r"[A-Z]{1,5}(\.[A-Z]{1,2})?"


def _clean_num(v) -> float | None:
    """Strip $, commas, % and parentheses-negatives from a broker numeric cell."""
    if pd.isna(v):
        return None
    s = str(v).strip().replace("$", "").replace(",", "").replace("%", "")
    if s in ("", "--", "n/a", "N/A", "nan"):
        return None
    if s.startswith("(") and s.endswith(")"):  # accounting-style negative
        s = "-" + s[1:-1]
    try:
        return float(s)
    except ValueError:
        return None


def parse_holdings(raw: str | bytes) -> pd.DataFrame:
    """Parse a holdings CSV into a clean ticker/shares/cost_basis DataFrame.

    Handles both the app's simple ``ticker,shares,cost_basis`` format and real
    broker exports (e.g. Fidelity's Portfolio_Positions file, which uses
    Symbol/Quantity/Average Cost Basis, includes a money-market cash row, dollar
    signs, and trailing disclaimer paragraphs). Non-equity rows (cash/money
    market like ``SPAXX**``, "Pending Activity", and footer text) are dropped.
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8-sig", errors="ignore")
    # on_bad_lines="skip" tolerates the ragged disclaimer footer Fidelity appends.
    # index_col=False stops pandas from treating the first column as a row index
    # when rows have a trailing comma (Fidelity rows do), which would shift columns.
    df = pd.read_csv(io.StringIO(raw), on_bad_lines="skip", skip_blank_lines=True, index_col=False)
    df.columns = [str(c).strip().lower() for c in df.columns]

    # Resolve which source column maps to each canonical field.
    resolved: dict[str, str] = {}
    for canon, options in _COLUMN_ALIASES.items():
        for opt in options:
            if opt in df.columns:
                resolved[canon] = opt
                break
    # Fallback for headerless/simple files: assume the first column is the ticker.
    ticker_col = resolved.get("ticker", df.columns[0])

    out = pd.DataFrame()
    out["ticker"] = df[ticker_col].astype("string").str.strip().str.upper()
    out["shares"] = (
        df[resolved["shares"]].map(_clean_num) if "shares" in resolved else 0.0
    )
    out["cost_basis"] = (
        df[resolved["cost_basis"]].map(_clean_num) if "cost_basis" in resolved else pd.NA
    )
    out["shares"] = pd.to_numeric(out["shares"], errors="coerce").fillna(0.0)
    out["cost_basis"] = pd.to_numeric(out["cost_basis"], errors="coerce")

    # Keep only rows whose ticker looks like a real stock/ETF symbol. This drops
    # money-market funds (SPAXX**), "Pending Activity", account-name rows, and
    # the disclaimer footer that lands in the symbol column as junk/NaN.
    out = out[out["ticker"].str.fullmatch(_VALID_TICKER, na=False)]
    out["ticker"] = out["ticker"].astype(str)
    return out.reset_index(drop=True)


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
                "Analyst": a.analyst_rating or "—",
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
