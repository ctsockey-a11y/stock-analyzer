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

from analyzer import analysis, data, portfolio

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


def verdict_badge(a) -> str:
    return f"<span style='background:{score_color(a.composite)};color:#0e1117;padding:3px 10px;border-radius:6px;font-weight:700'>{a.verdict} · {a.composite:.0f}/100</span>"


# --------------------------------------------------------------------------- #
# Sidebar — holdings input
# --------------------------------------------------------------------------- #
st.sidebar.title("📈 Stock Analyzer")
st.sidebar.caption("Free equity research from yfinance, SEC EDGAR & news.")

st.sidebar.subheader("Your holdings")

# Holdings persist in this browser's local storage (private to your device).
local_store = LocalStorage()
saved_csv = None
try:
    saved_csv = local_store.getItem("saved_holdings")
except Exception:
    saved_csv = None

base_modes = ["Sample portfolio", "Upload CSV", "Type manually"]
modes = (["My saved holdings"] + base_modes) if saved_csv else base_modes
mode = st.sidebar.radio("Load holdings from:", modes, label_visibility="collapsed")

holdings_df = None
if mode == "My saved holdings":
    holdings_df = portfolio.parse_holdings(saved_csv)
    st.sidebar.caption("✓ Loaded from this browser.")
    if st.sidebar.button("🗑️ Clear saved holdings", use_container_width=True):
        local_store.deleteItem("saved_holdings")
        st.rerun()
elif mode == "Sample portfolio":
    holdings_df = portfolio.parse_holdings(open("data/sample_holdings.csv").read())
elif mode == "Upload CSV":
    up = st.sidebar.file_uploader("CSV with columns: ticker, shares, cost_basis", type="csv")
    if up is not None:
        holdings_df = portfolio.parse_holdings(up.getvalue())
        # Auto-save to this browser, but only when the parsed holdings actually
        # change — guards against re-saving on every rerun.
        if not holdings_df.empty:
            csv_str = holdings_df.to_csv(index=False)
            if st.session_state.get("_saved_holdings_csv") != csv_str:
                local_store.setItem("saved_holdings", csv_str, key="autosave_holdings")
                st.session_state["_saved_holdings_csv"] = csv_str
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

# Manual save for the sample/manual modes (Upload auto-saves above).
if mode in ("Sample portfolio", "Type manually") and holdings_df is not None and not holdings_df.empty:
    if st.sidebar.button("💾 Save these holdings to this browser", use_container_width=True):
        local_store.setItem("saved_holdings", holdings_df.to_csv(index=False), key="save_holdings")
        st.session_state["_saved_holdings_csv"] = holdings_df.to_csv(index=False)
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
tab_portfolio, tab_stock, tab_screen = st.tabs(
    ["💼 My Portfolio", "🔬 Analyze a Stock", "🚀 Opportunity Screener"]
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
        "Ranks a list by an **opportunity score** that tilts toward growth + smart-money "
        "conviction at a not-yet-stretched valuation. High score ≠ guaranteed return — "
        "treat it as a research starting point, not advice."
    )
    default_universe = "NVDA,AMD,PLTR,SMCI,TSLA,META,AVGO,MU,CRWD,SHOP,UBER,SOFI,COIN,ARM,DELL"
    universe = st.text_area("Tickers to scan (comma-separated)", value=default_universe, height=80)
    if st.button("🚀 Run screener", type="primary"):
        tickers = tuple(t.strip().upper() for t in universe.replace("\n", ",").split(",") if t.strip())
        with st.spinner(f"Scanning {len(tickers)} tickers… (first run pulls live data)"):
            res = cached_screen(tickers)
        if res.empty:
            st.warning("No results — check the tickers.")
        else:
            st.success(f"Ranked {len(res)} stocks by opportunity.")
            st.dataframe(
                res.style.format({"Price": "${:,.2f}", "Opportunity": "{:.0f}", "Composite": "{:.0f}"}, na_rep="—")
                .background_gradient(subset=["Opportunity"], cmap="RdYlGn", vmin=0, vmax=100),
                use_container_width=True,
                hide_index=True,
            )

st.sidebar.divider()
st.sidebar.caption(
    "⚠️ Educational tool, not financial advice. Data may be delayed or incomplete. "
    "Always do your own research."
)
