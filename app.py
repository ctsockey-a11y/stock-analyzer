"""Stock Holdings Analyzer — a free, mobile-friendly equity research dashboard.

Run locally:   streamlit run app.py
Deploy free:   push to GitHub -> share.streamlit.io  (see README.md)
"""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from streamlit_local_storage import LocalStorage

from analyzer import analysis, data, portfolio, smartmoney

st.set_page_config(page_title="Stock Analyzer", page_icon="📈", layout="wide")


# --------------------------------------------------------------------------- #
# Cached wrappers (so the app stays snappy and is gentle on data sources)
# --------------------------------------------------------------------------- #
@st.cache_data(ttl=300, show_spinner=False)
def cached_analyze(ticker: str):
    return analysis.analyze(ticker)


@st.cache_data(ttl=300, show_spinner=False)
def cached_history(ticker: str, period: str = "1y"):
    return data.get_price_history(ticker, period)


@st.cache_data(ttl=300, show_spinner=False)
def cached_screen(tickers: tuple[str, ...]):
    return analysis.screen(list(tickers))


@st.cache_data(ttl=1800, show_spinner=False)
def cached_top_gainers():
    return tuple(data.get_top_gainers(15))


@st.cache_data(ttl=10800, show_spinner=False)  # 3h: filings update slowly
def cached_congress_trades(max_reports: int = 25):
    return data.get_congress_trades(max_reports)


@st.cache_data(ttl=10800, show_spinner=False)
def cached_senate_trades(max_reports: int = 20):
    return data.get_senate_trades(max_reports)


@st.cache_data(ttl=21600, show_spinner=False)  # 6h: 13F filings are quarterly
def cached_13f(cik: str):
    return data.get_13f_holdings(cik, 15)


@st.cache_data(ttl=10800, show_spinner=False)
def cached_congress_activity():
    return smartmoney.congress_activity()


@st.cache_data(ttl=21600, show_spinner=False)
def cached_fund_activity():
    return smartmoney.fund_activity()


@st.cache_data(ttl=300, show_spinner=False)
def cached_news(ticker: str, key: str | None):
    return data.get_news(ticker, key)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_filings(ticker: str):
    return data.get_sec_filings(ticker)


@st.cache_data(ttl=300, show_spinner=False)
def cached_institutional(ticker: str):
    return data.get_institutional_holders(ticker)


@st.cache_data(ttl=300, show_spinner=False)
def cached_insiders(ticker: str):
    return data.get_insider_transactions(ticker)


def finnhub_key() -> str | None:
    try:
        k = st.secrets.get("FINNHUB_KEY", "")
        return k or None
    except Exception:
        return None


def score_color(score: float) -> str:
    if score >= 75:
        return "#16c784"
    if score >= 60:
        return "#7ac74f"
    if score >= 45:
        return "#e0b500"
    if score >= 30:
        return "#e08e00"
    return "#ea3943"


def fmt_num(v) -> str:
    return f"{v:,.2f}" if v is not None and not pd.isna(v) else "—"


def fmt_pct(v) -> str:
    return f"{v * 100:.1f}%" if v is not None and not pd.isna(v) else "—"


def fmt_int(v) -> str:
    return f"{v:,.0f}" if v is not None and not pd.isna(v) else "—"


def fmt_big_money(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    for unit, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if abs(v) >= unit:
            return f"${v / unit:.2f}{suffix}"
    return f"${v:,.0f}"


def opportunity_bg(v) -> str:
    """Red→yellow→green cell background for a 0-100 score.

    Hand-rolled so we don't need matplotlib (which pandas' background_gradient
    requires and which isn't installed on the cloud).
    """
    if pd.isna(v):
        return ""
    t = max(0.0, min(1.0, float(v) / 100))
    if t < 0.5:
        f = t / 0.5
        r, g, b = 234 + (224 - 234) * f, 57 + (181 - 57) * f, 67 + (0 - 67) * f
    else:
        f = (t - 0.5) / 0.5
        r, g, b = 224 + (22 - 224) * f, 181 + (199 - 181) * f, 0 + (132 - 0) * f
    return f"background-color: rgba({int(r)},{int(g)},{int(b)},0.35)"


def verdict_badge(a) -> str:
    return f"<span style='background:{score_color(a.composite)};color:#0e1117;padding:3px 10px;border-radius:6px;font-weight:700'>{a.verdict} · {a.composite:.0f}/100</span>"


# --------------------------------------------------------------------------- #
# Sidebar — holdings input
# --------------------------------------------------------------------------- #
st.sidebar.title("📈 Stock Analyzer")
st.sidebar.caption("Free equity research from yfinance, SEC EDGAR & news.")

st.sidebar.subheader("Your holdings")

# Holdings persist in this browser's local storage (private to your device).
# IMPORTANT: read storage only ONCE per session and cache it. Calling getItem on
# every rerun conflicts with setItem on the same key and silently blocks
# overwrites (this is why re-uploading didn't replace the saved holdings).
local_store = LocalStorage()
if "ls_loaded" not in st.session_state:
    try:
        st.session_state["ls_holdings"] = local_store.getItem("saved_holdings")
    except Exception:
        st.session_state["ls_holdings"] = None
    st.session_state["ls_loaded"] = True
saved_csv = st.session_state.get("ls_holdings")


def _save_holdings(csv_str: str):
    """Write holdings to browser storage and update the in-session cache."""
    n = st.session_state.get("ls_set_n", 0) + 1
    st.session_state["ls_set_n"] = n
    local_store.setItem("saved_holdings", csv_str, key=f"ls_set_{n}")
    st.session_state["ls_holdings"] = csv_str


base_modes = ["Sample portfolio", "Upload CSV", "Type manually"]
modes = (["My saved holdings"] + base_modes) if saved_csv else base_modes
mode = st.sidebar.radio("Load holdings from:", modes, label_visibility="collapsed")

holdings_df = None
if mode == "My saved holdings":
    holdings_df = portfolio.parse_holdings(saved_csv)
    st.sidebar.caption("✓ Loaded from this browser.")
    if st.sidebar.button("🗑️ Clear saved holdings", use_container_width=True):
        local_store.deleteItem("saved_holdings")
        st.session_state["ls_holdings"] = None
        st.rerun()
elif mode == "Sample portfolio":
    holdings_df = portfolio.parse_holdings(open("data/sample_holdings.csv").read())
elif mode == "Upload CSV":
    up = st.sidebar.file_uploader("CSV with columns: ticker, shares, cost_basis", type="csv")
    if up is not None:
        holdings_df = portfolio.parse_holdings(up.getvalue())
        # Auto-save on upload, only when the parsed holdings actually change.
        if not holdings_df.empty:
            csv_str = holdings_df.to_csv(index=False)
            if st.session_state.get("ls_holdings") != csv_str:
                _save_holdings(csv_str)
                st.sidebar.success("✓ Saved to this browser — loads automatically next time.")
            else:
                st.sidebar.caption("✓ Saved to this browser.")
    st.sidebar.caption("Tip: most brokers can export a positions CSV.")
else:
    txt = st.sidebar.text_area(
        "One per line: TICKER,SHARES,COST",
        value="AAPL,25,150\nNVDA,15,95.5\nMSFT,10,310",
        height=120,
    )
    holdings_df = portfolio.parse_holdings("ticker,shares,cost_basis\n" + txt)

# Reliable manual save for any non-saved mode (a backstop to the auto-save).
if mode != "My saved holdings" and holdings_df is not None and not holdings_df.empty:
    if st.sidebar.button("💾 Save these holdings to this browser", use_container_width=True):
        _save_holdings(holdings_df.to_csv(index=False))
        st.sidebar.success("Saved! They'll load automatically next time on this device.")

if finnhub_key():
    st.sidebar.success("News: Finnhub key active")
else:
    st.sidebar.info("News: free Yahoo headlines (add a Finnhub key for sentiment)")

# Data is cached for 5 minutes for speed; this button force-pulls live data now.
if st.sidebar.button("🔄 Refresh data", use_container_width=True, help="Clear the cache and re-fetch live prices, fundamentals & news"):
    st.cache_data.clear()
    st.rerun()
st.sidebar.caption("Data auto-refreshes every 5 min; click to pull live now.")


# --------------------------------------------------------------------------- #
# Main tabs
# --------------------------------------------------------------------------- #
tab_portfolio, tab_stock, tab_screen, tab_congress, tab_funds = st.tabs(
    ["💼 My Portfolio", "🔬 Analyze a Stock", "🚀 Opportunity Screener",
     "🏛️ Congress Trades", "🏦 Big Investors"]
)

# ---- Portfolio tab -------------------------------------------------------- #
with tab_portfolio:
    st.header("Portfolio health")
    if holdings_df is None or holdings_df.empty:
        st.info("Add some holdings in the sidebar to see your portfolio analysis.")
    else:
        with st.spinner("Analyzing your holdings…"):
            pos, summary, _ = portfolio.analyze_portfolio(holdings_df)
        if pos.empty:
            st.warning("Couldn't fetch data for those tickers. Check the symbols.")
        else:
            r1c1, r1c2, r1c3 = st.columns(3)
            r1c1.metric("Total value", f"${summary['total_value']:,.0f}")
            total_pl = summary.get("total_pl")
            total_pl_pct = summary.get("total_pl_pct")
            if total_pl is not None:
                r1c2.metric(
                    "Total P&L (vs cost)",
                    f"${total_pl:+,.0f}",
                    delta=f"{total_pl_pct:+.1f}%" if total_pl_pct is not None else None,
                )
            else:
                r1c2.metric("Total P&L (vs cost)", "—", help="Add a cost basis column to see this")
            day_pl = summary.get("day_pl")
            day_pl_pct = summary.get("day_pl_pct")
            if day_pl is not None:
                r1c3.metric(
                    "Today's P&L",
                    f"${day_pl:+,.0f}",
                    delta=f"{day_pl_pct:+.2f}%" if day_pl_pct is not None else None,
                )
            else:
                r1c3.metric("Today's P&L", "—")

            r2c1, r2c2, r2c3 = st.columns(3)
            r2c1.metric("Positions", summary["positions"])
            r2c2.metric("Weighted health", f"{summary['weighted_health']:.0f}/100")
            r2c3.metric("Top holding weight", f"{summary['concentration']:.0f}%")

            for w in portfolio.portfolio_warnings(summary):
                st.warning(w)

            st.subheader("Positions")

            def _day_color(v):
                if pd.isna(v):
                    return ""
                return "color: #16c784" if v > 0 else ("color: #ea3943" if v < 0 else "")

            st.dataframe(
                pos.style.format(
                    {
                        "Price": "${:,.2f}",
                        "Day $": "${:+,.0f}",
                        "Day %": "{:+.2f}%",
                        "Value": "${:,.0f}",
                        "Gain %": "{:+.1f}%",
                        "Weight %": "{:.1f}%",
                        "Health": "{:.0f}",
                    },
                    na_rep="—",
                ).map(_day_color, subset=["Day $", "Day %"]),
                use_container_width=True,
                hide_index=True,
            )

            cc1, cc2 = st.columns(2)
            with cc1:
                fig = px.pie(pos, values="Value", names="Ticker", title="Allocation by position", hole=0.45)
                fig.update_layout(height=340, margin=dict(t=40, b=0, l=0, r=0))
                st.plotly_chart(fig, use_container_width=True)
            with cc2:
                # Allocation by sector — leans on the refined crypto/AI/quantum labels.
                sec = pos.groupby("Sector", as_index=False)["Value"].sum()
                figs = px.pie(sec, values="Value", names="Sector", title="Allocation by sector", hole=0.45)
                figs.update_layout(height=340, margin=dict(t=40, b=0, l=0, r=0))
                st.plotly_chart(figs, use_container_width=True)

            cc3, cc4 = st.columns(2)
            with cc3:
                dp = pos.dropna(subset=["Day $"]).copy()
                if not dp.empty:
                    dp["dir"] = dp["Day $"].apply(lambda v: "up" if v >= 0 else "down")
                    figd = px.bar(
                        dp, x="Ticker", y="Day $", title="Today's P&L by holding ($)",
                        color="dir", color_discrete_map={"up": "#16c784", "down": "#ea3943"},
                    )
                    figd.update_layout(height=340, margin=dict(t=40, b=0, l=0, r=0), showlegend=False)
                    st.plotly_chart(figd, use_container_width=True)
            with cc4:
                health = pos[["Ticker", "Health"]].copy()
                fig2 = px.bar(
                    health, x="Ticker", y="Health", title="Health score by holding",
                    color="Health", color_continuous_scale=["#ea3943", "#e0b500", "#16c784"],
                    range_color=[0, 100],
                )
                fig2.update_layout(height=340, margin=dict(t=40, b=0, l=0, r=0))
                st.plotly_chart(fig2, use_container_width=True)

            # Smart-money check: flag holdings recently traded by Congress or held by big funds.
            st.subheader("🔎 Smart-money activity in your holdings")
            st.caption("Cross-checks your tickers against recent Congress trades and famous funds' 13F holdings.")
            if st.button("Check Congress & big-investor activity", key="sm_check"):
                with st.spinner("Cross-referencing Congress filings & fund 13Fs… (~1 min first time)"):
                    cong = cached_congress_activity()
                    funds = cached_fund_activity()
                hits = 0
                for tk in pos["Ticker"]:
                    c = cong.get(tk)
                    f = funds.get(tk)
                    if not c and not f:
                        continue
                    hits += 1
                    bits = []
                    if c:
                        if c["buys"]:
                            bits.append(f"🏛️ **{c['buys']}** Congress buy(s)")
                        if c["sells"]:
                            bits.append(f"🏛️ **{c['sells']}** Congress sell(s)")
                        bits.append("— " + ", ".join(c["actors"][:3]))
                    if f:
                        bits.append(f"🏦 held by **{', '.join(f)}**")
                    st.markdown(f"**{tk}**: " + " · ".join(bits))
                if not hits:
                    st.info("None of your holdings show up in recent Congress trades or the tracked funds' 13Fs. "
                            "(Your names are small-caps that big funds rarely hold and Congress hasn't traded recently.)")

# ---- Single-stock deep dive ----------------------------------------------- #
with tab_stock:
    st.header("Deep-dive analysis")
    ticker = st.text_input("Ticker", value="NVDA", max_chars=8).strip().upper()
    if ticker:
        with st.spinner(f"Analyzing {ticker}…"):
            a = cached_analyze(ticker)
        if not a.price and not a.info:
            st.error(f"No data found for '{ticker}'. Double-check the symbol.")
        else:
            top = st.columns([3, 1, 1])
            top[0].markdown(f"### {a.name}  \n*{a.sector}*")
            _day_delta = None
            if a.day_change is not None and a.day_change_pct is not None:
                _day_delta = f"{a.day_change:+,.2f} ({a.day_change_pct:+.2f}%) today"
            top[1].metric("Price", f"${a.price:,.2f}" if a.price else "—", delta=_day_delta)
            _arating = a.analyst_rating or "—"
            _ahelp = f"{a.analyst_bullish_pct:.0f}% of analysts bullish" if a.analyst_bullish_pct is not None else None
            top[2].metric("Analyst rating", _arating, help=_ahelp)
            st.markdown(verdict_badge(a), unsafe_allow_html=True)

            # 52-week range: where does today's price sit between the year's low and high?
            lo = a.info.get("fiftyTwoWeekLow")
            hi = a.info.get("fiftyTwoWeekHigh")
            if lo and hi and hi > lo and a.price:
                pos_pct = max(0.0, min(1.0, (a.price - lo) / (hi - lo)))
                st.caption(
                    f"**52-week range** — ${lo:,.2f} low · **${a.price:,.2f} now** "
                    f"({pos_pct*100:.0f}% of range) · ${hi:,.2f} high"
                )
                st.progress(pos_pct)

            # Pillar scores
            st.subheader("Why this score")
            pcols = st.columns(len(a.pillars))
            for col, p in zip(pcols, a.pillars):
                col.markdown(
                    f"<div style='text-align:center'><div style='font-size:1.6rem;font-weight:800;color:{score_color(p.score)}'>{p.score:.0f}</div>"
                    f"<div style='font-size:0.8rem;color:#aaa'>{p.name}</div></div>",
                    unsafe_allow_html=True,
                )

            g1, g2 = st.columns(2)
            with g1:
                st.markdown("**✅ Strengths**")
                reasons = a.all_reasons
                if reasons:
                    for r in reasons:
                        st.markdown(f"- {r}")
                else:
                    st.caption("No standout strengths detected.")
            with g2:
                st.markdown("**⚠️ Risks**")
                flags = a.all_flags
                if flags:
                    for f in flags:
                        st.markdown(f"- {f}")
                else:
                    st.caption("No major red flags detected.")

            # Key statistics — a fuller set of raw data points from Finnhub/yfinance.
            with st.expander("📊 Key statistics", expanded=True):
                info = a.info
                stats = [
                    ("Market cap", fmt_big_money(info.get("marketCap"))),
                    ("P/E (TTM)", fmt_num(info.get("trailingPE"))),
                    ("Forward P/E", fmt_num(info.get("forwardPE"))),
                    ("PEG", fmt_num(info.get("pegRatio"))),
                    ("P/S", fmt_num(info.get("priceToSalesTrailing12Months"))),
                    ("P/B", fmt_num(info.get("priceToBook"))),
                    ("EPS (TTM)", fmt_num(info.get("trailingEps"))),
                    ("Dividend yield", fmt_pct(info.get("dividendYield"))),
                    ("Beta", fmt_num(info.get("beta"))),
                    ("Gross margin", fmt_pct(info.get("grossMargins"))),
                    ("Operating margin", fmt_pct(info.get("operatingMargins"))),
                    ("Net margin", fmt_pct(info.get("profitMargins"))),
                    ("ROE", fmt_pct(info.get("returnOnEquity"))),
                    ("ROA", fmt_pct(info.get("returnOnAssets"))),
                    ("Revenue growth", fmt_pct(info.get("revenueGrowth"))),
                    ("Earnings growth", fmt_pct(info.get("earningsGrowth"))),
                    ("Debt / equity", fmt_num(info.get("debtToEquity"))),
                    ("Current ratio", fmt_num(info.get("currentRatio"))),
                    ("Quick ratio", fmt_num(info.get("quickRatio"))),
                    ("Avg volume (10d)", fmt_int(info.get("averageVolume"))),
                ]
                cols = st.columns(4)
                for i, (label, val) in enumerate(stats):
                    cols[i % 4].markdown(
                        f"<div style='font-size:0.75rem;color:#888'>{label}</div>"
                        f"<div style='font-size:1.05rem;font-weight:600;margin-bottom:10px'>{val}</div>",
                        unsafe_allow_html=True,
                    )
                st.markdown("**Price returns**")
                rets = [("5-day", info.get("_ret5d")), ("13-week", info.get("_ret13w")),
                        ("YTD", info.get("_retytd")), ("52-week", info.get("_ret52w"))]
                rcols = st.columns(len(rets))
                for col, (lbl, v) in zip(rcols, rets):
                    color = "#16c784" if (v or 0) > 0 else ("#ea3943" if (v or 0) < 0 else "#888")
                    disp = f"{v:+.1f}%" if v is not None else "—"
                    col.markdown(
                        f"<div style='font-size:0.75rem;color:#888'>{lbl}</div>"
                        f"<div style='font-size:1.1rem;font-weight:700;color:{color}'>{disp}</div>",
                        unsafe_allow_html=True,
                    )

            # Price chart
            hist = cached_history(ticker, "1y")
            if not hist.empty:
                close = hist["Close"].dropna()
                ma = close.rolling(min(200, len(close))).mean()
                fig = go.Figure()
                fig.add_trace(go.Scatter(x=close.index, y=close, name="Price", line=dict(color="#16c784")))
                fig.add_trace(go.Scatter(x=ma.index, y=ma, name="200-day avg", line=dict(color="#888", dash="dot")))
                fig.update_layout(height=320, title="1-year price", margin=dict(t=40, b=0, l=0, r=0))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("📉 Price chart unavailable here (Yahoo blocks cloud servers). Momentum is scored from Finnhub data instead.")

            # Smart money + news in expandable sections
            with st.expander("🏦 Who's buying — institutions & insiders", expanded=True):
                inst = cached_institutional(ticker)
                if isinstance(inst, pd.DataFrame) and not inst.empty:
                    st.markdown("**Top institutional holders**")
                    st.dataframe(inst.head(8), use_container_width=True, hide_index=True)
                ins = cached_insiders(ticker)
                if isinstance(ins, pd.DataFrame) and not ins.empty:
                    st.markdown("**Recent insider transactions (Form 4)**")
                    st.dataframe(ins.head(8), use_container_width=True, hide_index=True)
                if (not isinstance(inst, pd.DataFrame) or inst.empty) and (not isinstance(ins, pd.DataFrame) or ins.empty):
                    st.caption("No institutional/insider data available.")

            with st.expander("📰 Recent news"):
                for n in cached_news(ticker, finnhub_key()):
                    when = n["datetime"].strftime("%b %d") if n.get("datetime") else ""
                    title = n["title"] or "(untitled)"
                    if n.get("url"):
                        st.markdown(f"- [{title}]({n['url']}) · *{n['publisher']}* {when}")
                    else:
                        st.markdown(f"- {title} · *{n['publisher']}* {when}")

            with st.expander("🗂️ Latest SEC filings"):
                filings = cached_filings(ticker)
                if filings:
                    fdf = pd.DataFrame(filings)
                    st.dataframe(fdf, use_container_width=True, hide_index=True)
                else:
                    st.caption("No SEC filings found (non-US listing or mapping miss).")

# ---- Opportunity screener -------------------------------------------------- #
with tab_screen:
    st.header("Find high-potential stocks")
    st.caption(
        "Scans a ready-made universe and ranks it by an **opportunity score** that tilts "
        "toward growth + smart-money conviction at a not-yet-stretched valuation. "
        "High score ≠ guaranteed return — a research starting point, not advice."
    )

    GAINERS_OPT = "🔥 Today's top gainers (live)"
    CONGRESS_OPT = "🏛️ What Congress recently bought"
    FUNDS_OPT = "🏦 What big investors hold"
    category = st.selectbox(
        "Pick a universe to scan (no tickers needed):",
        [GAINERS_OPT, CONGRESS_OPT, FUNDS_OPT] + list(analysis.SCREENER_UNIVERSES.keys()),
    )
    if category == GAINERS_OPT:
        st.caption("Pulls the market's biggest movers today (Alpha Vantage). ⚠️ Day-gainers are often "
                   "thin, speculative small-caps — treat with extra caution.")
    elif category == CONGRESS_OPT:
        st.caption("Analyzes the stocks members of Congress have **bought** in their recent disclosures.")
    elif category == FUNDS_OPT:
        st.caption("Analyzes the most widely-held stocks across the tracked famous funds' latest 13Fs.")
    smart_on = st.checkbox("Factor in Congress & big-investor activity (adds ~1 min on first run)", value=True)
    with st.expander("⚙️ Or scan your own custom list instead"):
        custom = st.text_area("Tickers (comma-separated) — overrides the universe above", value="", height=70)

    if st.button("🔎 Find opportunities", type="primary"):
        cong = funds_map = {}
        if custom.strip():
            tickers = tuple(t.strip().upper() for t in custom.replace("\n", ",").split(",") if t.strip())
            label = "your custom list"
        elif category == GAINERS_OPT:
            with st.spinner("Fetching today's top gainers…"):
                tickers = cached_top_gainers()
            label = "today's top gainers"
            if not tickers:
                st.warning("Couldn't fetch top gainers right now (the free Alpha Vantage tier may be rate-limited). "
                           "Add your own free key in Settings → Secrets as ALPHAVANTAGE_KEY for reliable access.")
        elif category == CONGRESS_OPT:
            with st.spinner("Reading recent Congress filings…"):
                cong = cached_congress_activity()
            tickers = tuple(tk for tk, a in sorted(cong.items(), key=lambda kv: -kv[1]["buys"]) if a["buys"] > 0)[:25]
            label = "Congress's recent buys"
        elif category == FUNDS_OPT:
            with st.spinner("Reading famous funds' 13F filings…"):
                funds_map = cached_fund_activity()
            tickers = tuple(tk for tk, fs in sorted(funds_map.items(), key=lambda kv: -len(kv[1])))[:25]
            label = "big-investor holdings"
        else:
            tickers = tuple(analysis.SCREENER_UNIVERSES[category])
            label = category

        with st.spinner(f"Scanning {len(tickers)} stocks in {label}… first run pulls live data (~1-2 min)."):
            res = cached_screen(tickers)
        if res.empty:
            st.warning("No results — try again in a moment (the free data API may be rate-limited).")
        else:
            # Layer in smart-money signal: a flag column + an opportunity-score nudge.
            if smart_on:
                with st.spinner("Adding Congress & big-investor signals…"):
                    cong = cong or cached_congress_activity()
                    funds_map = funds_map or cached_fund_activity()
                res["Smart $"] = res["Ticker"].map(lambda tk: smartmoney.flag(tk, cong, funds_map))
                res["Opportunity"] = (
                    res["Opportunity"] + res["Ticker"].map(lambda tk: smartmoney.score_bonus(tk, cong, funds_map))
                ).clip(0, 100).round(1)
                res = res.sort_values("Opportunity", ascending=False).reset_index(drop=True)

            st.success(f"Top opportunities in {label} — ranked by opportunity score"
                       + (" (incl. smart-money)." if smart_on else "."))
            best = res.iloc[0]
            st.markdown(
                f"🏆 **Top pick: {best['Ticker']}** ({best['Name']}) — opportunity {best['Opportunity']:.0f}/100, "
                f"{best['Verdict']}. *{best['Top reason']}*"
            )
            fmt = {"Price": "${:,.2f}", "Opportunity": "{:.0f}", "Composite": "{:.0f}",
                   "Mkt cap": fmt_big_money, "P/E": "{:,.1f}", "Rev gr %": "{:+.0f}%"}
            st.dataframe(
                res.style.format(fmt, na_rep="—").map(opportunity_bg, subset=["Opportunity"]),
                use_container_width=True,
                hide_index=True,
            )

# ---- Congress trades ------------------------------------------------------- #
def _txn_color(v):
    s = str(v)
    if "Buy" in s:
        return "color: #16c784"
    if "Sell" in s:
        return "color: #ea3943"
    return ""


with tab_congress:
    st.header("🏛️ Congressional stock trades")
    st.caption(
        "Recent trades by members of Congress, parsed live from the official disclosure systems "
        "([House Clerk](https://disclosures-clerk.house.gov) & [Senate eFD](https://efdsearch.senate.gov)) — "
        "free, no API. Reported under the STOCK Act with a **lag** (~30-45 days); amounts are **ranges**."
    )
    chamber = st.radio("Chamber", ["🏛️ House (Representatives)", "🏦 Senate (Senators)"],
                       horizontal=True, label_visibility="collapsed")
    filt_ticker = st.text_input("Filter by ticker (optional)", value="", key="cong_filt").strip().upper()
    if st.button("Load recent trades", type="primary", key="load_congress"):
        is_house = chamber.startswith("🏛️")
        with st.spinner(f"Reading the latest {'House' if is_house else 'Senate'} disclosure filings… (~20-40s first time)"):
            trades = cached_congress_trades(25) if is_house else cached_senate_trades(20)
        if filt_ticker:
            trades = [t for t in trades if t["ticker"] == filt_ticker]
        if not trades:
            st.warning("No trades parsed right now"
                       + (f" for {filt_ticker}." if filt_ticker else " (filings may be momentarily unavailable)."))
        else:
            cols = ["filed", "member", "state", "type", "ticker", "amount"] if is_house \
                else ["filed", "member", "date", "type", "ticker", "amount"]
            tdf = pd.DataFrame(trades)[cols]
            tdf.columns = ["Filed", "Member", "State" if is_house else "Txn date", "Type", "Ticker", "Amount"]
            st.success(f"Showing {len(tdf)} trades from the {tdf['Member'].nunique()} most recent filers.")
            st.dataframe(tdf.style.map(_txn_color, subset=["Type"]), use_container_width=True, hide_index=True)
            top = pd.Series([t["ticker"] for t in trades]).value_counts().head(8)
            if not top.empty:
                st.markdown("**Most-active tickers in recent filings:** " +
                            " · ".join(f"`{tk}` ({n})" for tk, n in top.items()))

# ---- Big investors (13F) --------------------------------------------------- #
with tab_funds:
    st.header("🏦 What big investors own")
    st.caption(
        "Latest **13F holdings** of famous funds, from free SEC EDGAR filings. Funds managing >$100M "
        "must disclose US equity holdings quarterly — but with a **~45-day lag**, and 13F shows long "
        "positions only (no shorts/options detail). Values are total position size."
    )
    fund = st.selectbox("Pick an investor", list(data.FAMOUS_FUNDS.keys()))
    if st.button("📂 Load latest 13F holdings", type="primary"):
        with st.spinner(f"Fetching {fund}'s latest 13F from SEC EDGAR…"):
            res = cached_13f(data.FAMOUS_FUNDS[fund])
        if not res.get("holdings"):
            st.warning("Couldn't load holdings right now — try again in a moment.")
        else:
            st.success(f"**{fund}** — 13F filed {res['filed']} · {res['positions']} positions · "
                       f"${res['total'] / 1e9:,.1f}B total reported value.")
            hdf = pd.DataFrame(res["holdings"])
            hdf = hdf.rename(columns={"issuer": "Company", "value": "Value", "pct": "% of portfolio", "shares": "Shares"})
            st.dataframe(
                hdf.style.format({"Value": fmt_big_money, "% of portfolio": "{:.1f}%", "Shares": "{:,.0f}"})
                .bar(subset=["% of portfolio"], color="#16c784"),
                use_container_width=True, hide_index=True,
            )
            st.caption("Tip: paste any of these tickers' companies into the 🔬 Analyze a Stock tab for a full breakdown.")

st.sidebar.divider()
st.sidebar.caption(
    "⚠️ Educational tool, not financial advice. Data may be delayed or incomplete. "
    "Always do your own research."
)
