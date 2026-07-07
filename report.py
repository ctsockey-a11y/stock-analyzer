"""Smart Money Weekly — newsletter issue generator.

Builds a ready-to-paste markdown issue from the same pipelines the app uses:
  * US House + Senate stock trades (official Clerk / eFD disclosures)
  * Famous-fund 13F holdings (SEC EDGAR)
  * The 5-pillar rule-based scoring engine (the "quant check")

Run it locally (yfinance works here; no API key needed):

    source venv/bin/activate
    python report.py                 # last 10 days of filings, top 6 quant checks
    python report.py --days 14 --top 8

Output lands in reports/YYYY-MM-DD-smart-money-weekly.md. Everything above the
PAYWALL marker is the free preview; everything below is for paid subscribers.
The format deliberately avoids markdown tables — Substack's editor won't render
them on paste, so sections use bold lines and bullets instead.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from pathlib import Path

from analyzer import analysis, data, smartmoney

APP_URL = "https://stock-analyzer-b6zw4vfqkwqhynhvjktrbn.streamlit.app/"
PAYWALL = "\n---\n\n*✂️ — free preview ends here. Full quant breakdowns, fund watch, and the conviction-overlap list are for paid subscribers.*\n\n---\n"


# --------------------------------------------------------------------------- #
# Gather + aggregate
# --------------------------------------------------------------------------- #
def _parse_date(s: str) -> dt.date | None:
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime((s or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


def _amount_hi(amount: str) -> int:
    """Upper bound of a disclosed range like '$1,001 - $15,000' (0 if unparseable)."""
    nums = re.findall(r"\$([\d,]+)", amount or "")
    try:
        return int(nums[-1].replace(",", ""))
    except (IndexError, ValueError):
        return 0


def _fmt_money(n: int) -> str:
    if n >= 1_000_000:
        return f"${n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"${n / 1_000:.0f}K"
    return f"${n}"


def gather_trades(days: int, house_reports: int, senate_reports: int) -> tuple[list[dict], dt.date | None]:
    """All House+Senate trades filed within the window, tagged with chamber."""
    cutoff = dt.date.today() - dt.timedelta(days=days)
    house = data.get_congress_trades(house_reports)
    senate = data.get_senate_trades(senate_reports)
    if not senate:  # the eFD handshake is flaky; one retry usually recovers it
        print("  Senate fetch came back empty — retrying once…")
        senate = data.get_senate_trades(senate_reports)
    print(f"  House: {len(house)} trades · Senate: {len(senate)} trades"
          + ("  ⚠️ a chamber returned nothing — issue may be incomplete!" if not house or not senate else ""))
    trades = []
    for t in house:
        t["chamber"] = "House"
        trades.append(t)
    for t in senate:
        t["chamber"] = "Senate"
        trades.append(t)
    kept, oldest = [], None
    for t in trades:
        filed = _parse_date(t.get("filed", ""))
        # Keep undated rows rather than silently dropping a disclosed trade.
        if filed is None or filed >= cutoff:
            kept.append(t)
            if filed and (oldest is None or filed < oldest):
                oldest = filed
    return kept, oldest


def aggregate(trades: list[dict]) -> dict[str, dict]:
    """ticker -> buy/sell counts, summed range upper-bounds, and who did which."""
    agg: dict[str, dict] = {}
    for t in trades:
        a = agg.setdefault(t["ticker"], {"buys": 0, "sells": 0, "buy_hi": 0, "sell_hi": 0,
                                         "buyers": set(), "sellers": set()})
        label = f"{'Rep.' if t['chamber'] == 'House' else 'Sen.'} {t['member']}"
        if "Buy" in t["type"]:
            a["buys"] += 1
            a["buy_hi"] += _amount_hi(t.get("amount", ""))
            a["buyers"].add(label)
        elif "Sell" in t["type"]:
            a["sells"] += 1
            a["sell_hi"] += _amount_hi(t.get("amount", ""))
            a["sellers"].add(label)
    return agg


def _actors(a: dict, side: str) -> set[str]:
    return a["buyers" if side == "buys" else "sellers"]


def _ranked(agg: dict[str, dict], side: str, n: int = 8) -> list[tuple[str, dict]]:
    rows = [(tk, a) for tk, a in agg.items() if a[side]]
    rows.sort(key=lambda x: (x[1][side], len(_actors(x[1], side)), x[1][f"{side[:-1]}_hi"]), reverse=True)
    return rows[:n]


# --------------------------------------------------------------------------- #
# Render sections
# --------------------------------------------------------------------------- #
def _trade_line(tk: str, a: dict, side: str) -> str:
    n = a[side]
    hi = a["buy_hi" if side == "buys" else "sell_hi"]
    who = ", ".join(sorted(_actors(a, side)))
    amt = f", up to ~{_fmt_money(hi)} combined" if hi else ""
    verb = side[:-1] if n == 1 else side
    return f"- **{tk}** — {n} {verb}{amt} · {who}"


def congress_section(trades: list[dict], agg: dict, oldest: dt.date | None, days: int) -> str:
    window = f"filings disclosed since {oldest:%b %d}" if oldest else f"the last {days} days of filings"
    nh = sum(1 for t in trades if t["chamber"] == "House")
    ns = len(trades) - nh
    out = [f"## 🏛️ What Congress traded\n",
           f"*{len(trades)} trades ({nh} House · {ns} Senate) across {window}. "
           f"Amounts are the disclosed ranges' upper bounds.*\n"]
    bought, sold = _ranked(agg, "buys"), _ranked(agg, "sells")
    if bought:
        out.append("**Most bought**\n")
        out += [_trade_line(tk, a, "buys") for tk, a in bought]
        out.append("")
    if sold:
        out.append("**Most sold**\n")
        out += [_trade_line(tk, a, "sells") for tk, a in sold]
        out.append("")
    notable = sorted(trades, key=lambda t: _amount_hi(t.get("amount", "")), reverse=True)[:5]
    if notable and _amount_hi(notable[0].get("amount", "")):
        out.append("**Biggest single trades**\n")
        for t in notable:
            side = "bought" if "Buy" in t["type"] else ("sold" if "Sell" in t["type"] else "exchanged")
            who = f"{'Rep.' if t['chamber'] == 'House' else 'Sen.'} {t['member']}"
            out.append(f"- {who} {side} **{t['ticker']}** ({t.get('amount', '?')}) on {t.get('date', '?')}")
        out.append("")
    if not bought and not sold:
        out.append("*A quiet week — no parseable stock trades in the latest filings.*\n")
    return "\n".join(out)


def quant_section(agg: dict, top: int) -> tuple[str, list]:
    """Score the most-bought tickers with the 5-pillar engine."""
    picks = [tk for tk, _ in _ranked(agg, "buys", top)]
    out = ["## 🔬 Quant check: do the fundamentals agree?\n",
           "*Congress bought these — here's what our rule-based 5-pillar scoring engine "
           "(valuation, growth, profitability, financial health, smart-money & momentum) says. "
           "0–100; no AI, no vibes, every point traceable to a rule.*\n"]
    scored = []
    for tk in picks:
        try:
            a = analysis.analyze(tk)
        except Exception:
            continue
        scored.append(a)
        price = f" · ${a.price:,.2f}" if a.price else ""
        out.append(f"**{a.ticker} — {a.composite:.0f}/100 ({a.verdict})**{price} · {a.sector}")
        for r in a.all_reasons[:2]:
            out.append(f"- ✅ {r}")
        for f in a.all_flags[:2]:
            out.append(f"- ⚠️ {f}")
        out.append("")
    if not scored:
        out.append("*No scoreable buys this week.*\n")
    return "\n".join(out), scored


def funds_section() -> tuple[str, dict[str, list[str]]]:
    out = ["## 🏦 Big-investor watch\n",
           "*Top holdings from each fund's latest 13F filing (SEC EDGAR). 13Fs lag ~45 days — "
           "this is positioning, not today's trades.*\n"]
    fund_map = smartmoney.fund_activity()
    for fund, cik in data.FAMOUS_FUNDS.items():
        h = data.get_13f_holdings(cik, top=5)
        if not h.get("holdings"):
            continue
        tops = ", ".join(f"{x['issuer']} ({x['pct']:.0f}%)" for x in h["holdings"])
        filed = f" — filed {h['filed']}" if h.get("filed") else ""
        out.append(f"**{fund}**{filed}")
        out.append(f"- {tops}\n")
    return "\n".join(out), fund_map


def overlap_section(agg: dict, fund_map: dict[str, list[str]]) -> str:
    out = ["## 🎯 Conviction overlap\n",
           "*Tickers Congress just bought that ALSO sit in a famous fund's top holdings — "
           "two unrelated groups of informed money pointing the same way.*\n"]
    hits = [(tk, a, fund_map[tk]) for tk, a in agg.items() if a["buys"] and tk in fund_map]
    hits.sort(key=lambda x: (len(x[2]), x[1]["buys"]), reverse=True)
    if hits:
        for tk, a, funds in hits:
            n = a["buys"]
            out.append(f"- **{tk}** — {n} Congress buy{'s' if n > 1 else ''} "
                       f"({', '.join(sorted(a['buyers']))}) + held by {', '.join(funds)}")
    else:
        out.append("*No overlap this week — Congress and the big funds are shopping in different aisles.*")
    out.append("")
    return "\n".join(out)


def tldr(agg: dict, scored: list, fund_map: dict[str, list[str]]) -> str:
    lines = []
    bought = _ranked(agg, "buys", 1)
    if bought:
        tk, a = bought[0]
        nb, nm = a["buys"], len(a["buyers"])
        lines.append(f"- Congress's most-bought name: **{tk}** "
                     f"({nb} buy{'s' if nb > 1 else ''} by {nm} member{'s' if nm > 1 else ''}).")
    strong = [a for a in scored if a.composite >= 60]
    if strong:
        best = max(strong, key=lambda a: a.composite)
        lines.append(f"- Quant check's favorite of the bunch: **{best.ticker}** at {best.composite:.0f}/100 ({best.verdict}).")
    weak = [a for a in scored if a.composite < 45]
    if weak:
        worst = min(weak, key=lambda a: a.composite)
        lines.append(f"- Buyer beware: **{worst.ticker}** got bought anyway — our engine scores it {worst.composite:.0f}/100 ({worst.verdict}).")
    overlaps = [tk for tk, a in agg.items() if a["buys"] and tk in fund_map]
    if overlaps:
        lines.append(f"- Conviction overlap (Congress + famous funds): **{', '.join(sorted(overlaps)[:4])}**.")
    return "\n".join(lines) if lines else "- A quiet week in the disclosures."


FOOTER = f"""## The fine print

Congressional trades come from the official House Clerk and Senate eFD disclosure
systems; fund holdings from SEC EDGAR 13F filings. **Disclosures lag reality by
30–45 days** — treat everything here as positioning information, not trade signals.
Amounts are disclosed as ranges; we show upper bounds. Nothing in this letter is
investment advice; do your own research.

*Scores come from the free, open [Stock Analyzer]({APP_URL}) — run any ticker
through the same 5-pillar engine yourself.*
"""


# --------------------------------------------------------------------------- #
def build_issue(days: int, house_reports: int, senate_reports: int, top: int) -> str:
    today = dt.date.today()
    print(f"Fetching House + Senate filings (last {days} days)…")
    trades, oldest = gather_trades(days, house_reports, senate_reports)
    agg = aggregate(trades)
    print(f"  {len(trades)} trades, {len(agg)} tickers. Scoring top buys…")
    quant_md, scored = quant_section(agg, top)
    print("  Pulling 13F holdings…")
    funds_md, fund_map = funds_section()

    parts = [
        f"# 💰 Smart Money Weekly — {today:%B %-d, %Y}\n",
        "*What Congress and the world's most-watched investors just traded, "
        "cross-checked against cold, rule-based fundamentals.*\n",
        "**TL;DR**\n",
        tldr(agg, scored, fund_map),
        "",
        congress_section(trades, agg, oldest, days),
        PAYWALL,
        quant_md,
        funds_md,
        overlap_section(agg, fund_map),
        FOOTER,
    ]
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a Smart Money Weekly issue (markdown).")
    ap.add_argument("--days", type=int, default=10, help="only include filings disclosed in the last N days")
    ap.add_argument("--house", type=int, default=40, help="House PTR filings to parse")
    ap.add_argument("--senate", type=int, default=25, help="Senate PTR filings to parse")
    ap.add_argument("--top", type=int, default=6, help="most-bought tickers to run the quant check on")
    ap.add_argument("--out", default="reports", help="output directory")
    args = ap.parse_args()

    md = build_issue(args.days, args.house, args.senate, args.top)
    outdir = Path(args.out)
    outdir.mkdir(exist_ok=True)
    path = outdir / f"{dt.date.today():%Y-%m-%d}-smart-money-weekly.md"
    path.write_text(md)
    print(f"\n✅ Issue written to {path}")
    print("   Paste everything ABOVE the ✂️ marker as the free preview;")
    print("   the full issue is the paid post. (Format is Substack-paste-safe.)")


if __name__ == "__main__":
    main()
