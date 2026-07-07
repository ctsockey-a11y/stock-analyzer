"""Follow the Filings — newsletter issue generator.

Builds a ready-to-paste markdown issue from the same pipelines the app uses:
  * US House + Senate stock trades (official Clerk / eFD disclosures)
  * Famous-fund 13F holdings (SEC EDGAR)
  * The 5-pillar rule-based scoring engine (the "quant check")

Run it locally (yfinance works here; no API key needed):

    source venv/bin/activate
    python report.py                 # last 10 days of filings, top 6 quant checks
    python report.py --days 14 --top 8

Output lands in reports/YYYY-MM-DD-follow-the-filings.md. Everything above the
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


def _fmt_range(amount: str) -> str:
    """'$50,001 - $100,000' -> '$50K–$100K' (falls back to the raw string)."""
    nums = re.findall(r"\$([\d,]+)", amount or "")
    if len(nums) == 2:
        try:
            lo, hi = (int(x.replace(",", "")) for x in nums)
            return f"{_fmt_money(lo)}–{_fmt_money(hi)}"
        except ValueError:
            pass
    return amount or "?"


def _fmt_day(s: str) -> str:
    """'06/10/2026' -> 'Jun 10' (falls back to the raw string)."""
    d = _parse_date(s)
    return f"{d:%b %-d}" if d else (s or "?")


_VERDICT_DOT = {"Strong": "🟢", "Good": "🟢", "Mixed": "🟡", "Weak": "🔴", "Avoid": "🔴"}


def _bottom_line(a) -> str:
    """One plain-English takeaway per quant-checked stock."""
    if a.composite >= 75:
        return "the numbers strongly back this buy."
    if a.composite >= 60:
        return "solid fundamentals — the buy looks reasonable."
    if a.composite >= 45:
        return "a coin flip — real strengths, real question marks."
    return "the fundamentals don't support the enthusiasm."


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
EXPLAINER = (
    "> **New here?** US law makes this data public: the STOCK Act forces members of "
    "Congress to disclose their trades (within 45 days, as dollar ranges), and big funds "
    "must reveal their holdings quarterly in SEC 13F filings. We read the filings so you "
    "don't have to — then run every name through a rule-based scoring engine to see "
    "whether the fundamentals back the trade.\n"
)


def _trade_line(tk: str, a: dict, side: str) -> str:
    n = a[side]
    hi = a["buy_hi" if side == "buys" else "sell_hi"]
    who = " + ".join(sorted(_actors(a, side)))
    amt = f" totaling up to ~{_fmt_money(hi)}" if hi else ""
    verb = side[:-1] if n == 1 else side
    return f"- **{tk}** — {n} {verb}{amt} ({who})"


def congress_section(trades: list[dict], agg: dict, oldest: dt.date | None, days: int) -> str:
    window = f"since {oldest:%B %-d}" if oldest else f"in the last {days} days"
    nh = sum(1 for t in trades if t["chamber"] == "House")
    ns = len(trades) - nh
    out = [f"## 🏛️ What Congress traded\n",
           f"*{len(trades)} trades disclosed {window} ({nh} House, {ns} Senate).*\n"]
    bought, sold = _ranked(agg, "buys", 5), _ranked(agg, "sells", 5)
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
        out.append("**The biggest checks**\n")
        for t in notable:
            side = "bought" if "Buy" in t["type"] else ("sold" if "Sell" in t["type"] else "exchanged")
            who = f"{'Rep.' if t['chamber'] == 'House' else 'Sen.'} {t['member']}"
            out.append(f"- {who} {side} **{t['ticker']}** — {_fmt_range(t.get('amount', ''))} on {_fmt_day(t.get('date', ''))}")
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
        dot = _VERDICT_DOT.get(a.verdict, "⚪")
        out.append(f"### {dot} {a.ticker} — {a.composite:.0f}/100{price}")
        out.append(f"*{a.name} · {a.sector}*\n")
        for r in a.all_reasons[:2]:
            out.append(f"- ✅ {r}")
        for f in a.all_flags[:2]:
            out.append(f"- ⚠️ {f}")
        out.append(f"\n**Bottom line:** {_bottom_line(a)}\n")
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
        tops = " · ".join(f"{x['issuer']} {x['pct']:.0f}%" for x in h["holdings"])
        filed = _parse_date(h["filed"]) if h.get("filed") else None
        when = f" (filed {filed:%b %-d})" if filed else ""
        out.append(f"**{fund}**{when}")
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
            who = " and ".join(sorted(a["buyers"]))
            fl = ", ".join(funds[:-1]) + (" & " if len(funds) > 1 else "") + funds[-1]
            out.append(f"- **{tk}** — {who} bought it; {fl} hold{'s' if len(funds) == 1 else ''} it")
    else:
        out.append("*No overlap this week — Congress and the big funds are shopping in different aisles.*")
    out.append("")
    return "\n".join(out)


def lede(trades: list[dict], agg: dict, scored: list) -> str:
    """A two-sentence narrative hook built from this issue's actual numbers."""
    n = len(trades)
    bits = [f"Congress disclosed **{n} stock trades** in the latest filings."]
    by_tk = {a.ticker: a for a in scored}
    bought = _ranked(agg, "buys", 1)
    if bought:
        tk, _ = bought[0]
        a = by_tk.get(tk)
        if a:
            bits.append(f"Their favorite buy, **{tk}**, scores **{a.composite:.0f}/100** on our engine")
            best = max(scored, key=lambda x: x.composite)
            if best.ticker != tk and best.composite - a.composite >= 10:
                bits[-1] += f" — while a quieter buy, **{best.ticker}**, is the real standout at **{best.composite:.0f}/100**."
            else:
                bits[-1] += "."
    bits.append("Here's what they traded, what the numbers say, and where the politicians "
                "and the billionaire funds agree.")
    return " ".join(bits)


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


def takeaways(trades: list[dict], agg: dict, scored: list, fund_map: dict[str, list[str]]) -> str:
    """Data-derived summary + forward-looking watch list. Rules, not vibes."""
    out = ["## 🔮 The read-through\n",
           "*What this batch of filings actually says, and what we're watching next. "
           "Signals to research — not predictions, and not advice.*\n"]

    # -- What happened ------------------------------------------------------
    happened = []
    buys = sum(a["buys"] for a in agg.values())
    sells = sum(a["sells"] for a in agg.values())
    bhi = sum(a["buy_hi"] for a in agg.values())
    shi = sum(a["sell_hi"] for a in agg.values())
    if buys or sells:
        if bhi > shi * 1.3:
            lean = f"a net **buyer** (~{_fmt_money(bhi)} of buys vs ~{_fmt_money(shi)} of sells)"
        elif shi > bhi * 1.3:
            lean = f"a net **seller** (~{_fmt_money(shi)} of sells vs ~{_fmt_money(bhi)} of buys)"
        else:
            lean = f"roughly **balanced** ({buys} buys, {sells} sells)"
        happened.append(f"Congress was {lean} this window.")

    from collections import Counter
    members = Counter(t["member"] for t in trades)
    if members:
        top_member, top_n = members.most_common(1)[0]
        if len(trades) >= 10 and top_n / len(trades) > 0.4:
            happened.append(f"Honesty check: **{top_member}** accounts for {top_n} of the "
                            f"{len(trades)} trades — much of this window is one active "
                            "portfolio being rebalanced, so don't over-read the totals.")

    if scored:
        avg = sum(a.composite for a in scored) / len(scored)
        by_tk = {a.ticker: a for a in scored}
        fav = _ranked(agg, "buys", 1)
        fav_a = by_tk.get(fav[0][0]) if fav else None
        if fav_a and fav_a.composite < 50:
            happened.append(f"The crowd-favorite buy (**{fav_a.ticker}**) fails our quant "
                            f"check at {fav_a.composite:.0f}/100 — when the popular trade and the "
                            "fundamentals disagree, we side with the fundamentals.")
        elif avg >= 60:
            happened.append(f"Unusually disciplined batch: the buys average "
                            f"**{avg:.0f}/100** on the engine — Congress's picks and the "
                            "fundamentals mostly agree this time.")
        exp = [a for a in scored if any("Rich P/E" in f or "Very high P/S" in f for f in a.all_flags)]
        if len(exp) >= max(2, len(scored) // 2):
            happened.append(f"A theme across the buys: **paying up for growth** — "
                            f"{len(exp)} of {len(scored)} scored names carry rich-valuation flags. "
                            "That works while growth delivers and hurts fast when it doesn't.")
    if happened:
        out.append("**What happened**\n")
        out += [f"- {h}" for h in happened]
        out.append("")

    # -- What we're watching -------------------------------------------------
    watch = []
    for a in sorted(scored, key=lambda x: -x.composite):
        confirms = []
        if a.ticker in fund_map:
            confirms.append(f"held by {', '.join(fund_map[a.ticker][:2])}")
        buyers = agg.get(a.ticker, {}).get("buyers", set())
        if len(buyers) > 1:
            confirms.append(f"{len(buyers)} separate members bought")
        if a.composite >= 60 or confirms:
            why = f"scores {a.composite:.0f}/100"
            if confirms:
                why += "; " + " and ".join(confirms)
            risk = a.all_flags[0] if a.all_flags else None
            watch.append(f"- **{a.ticker}** — {why}." + (f" The thing to watch: {risk.lower()}." if risk else ""))
        if len(watch) >= 3:
            break
    if watch:
        out.append("**What we're watching into next issue**\n")
        out += watch
        out.append("\nThe most durable pattern in this data isn't any single trade — it's "
                   "**confluence**. When a disclosure, a big fund's book, and the fundamentals "
                   "all point the same way, that's the shortlist. When they disagree, that's "
                   "the warning.")
        out.append("")
    return "\n".join(out)


FOOTER = f"""## The fine print

Congressional trades come from the official House Clerk and Senate eFD disclosure
systems; fund holdings from SEC EDGAR 13F filings. **Disclosures lag reality by
30–45 days** — treat everything here as positioning information, not trade signals.
Amounts are disclosed as ranges; we show upper bounds. Nothing in this letter is
investment advice; do your own research.

*Scores come from the free, open [Stock Analyzer]({APP_URL}) — run any ticker
through the same 5-pillar engine yourself.*

Questions about a ticker or a trade? Hit reply — I read everything.
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
        f"# 🗂️ Follow the Filings — {today:%B %-d, %Y}\n",
        lede(trades, agg, scored),
        "",
        "**TL;DR**\n",
        tldr(agg, scored, fund_map),
        "",
        EXPLAINER,
        congress_section(trades, agg, oldest, days),
        PAYWALL,
        quant_md,
        funds_md,
        overlap_section(agg, fund_map),
        takeaways(trades, agg, scored, fund_map),
        FOOTER,
    ]
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a Follow the Filings issue (markdown).")
    ap.add_argument("--days", type=int, default=10, help="only include filings disclosed in the last N days")
    ap.add_argument("--house", type=int, default=40, help="House PTR filings to parse")
    ap.add_argument("--senate", type=int, default=25, help="Senate PTR filings to parse")
    ap.add_argument("--top", type=int, default=6, help="most-bought tickers to run the quant check on")
    ap.add_argument("--out", default="reports", help="output directory")
    args = ap.parse_args()

    md = build_issue(args.days, args.house, args.senate, args.top)
    outdir = Path(args.out)
    outdir.mkdir(exist_ok=True)
    path = outdir / f"{dt.date.today():%Y-%m-%d}-follow-the-filings.md"
    path.write_text(md)
    print(f"\n✅ Issue written to {path}")
    print("   Paste everything ABOVE the ✂️ marker as the free preview;")
    print("   the full issue is the paid post. (Format is Substack-paste-safe.)")


if __name__ == "__main__":
    main()
