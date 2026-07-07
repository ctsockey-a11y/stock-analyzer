"""Data fetching layer.

Data source strategy is built for free cloud hosting:
  * Finnhub  -> prices, fundamentals, insider trades, news (works from any IP
                with a free key; this is the PRIMARY source on Streamlit Cloud).
  * yfinance -> same fields, used as a fallback. Great locally, but Yahoo blocks
                cloud datacenter IPs, so it usually returns nothing when deployed.
  * Stooq    -> price history fallback (free, no key, not IP-blocked).
  * SEC EDGAR-> latest regulatory filings (no key, just a User-Agent; not blocked).

The Finnhub key is read from st.secrets["FINNHUB_KEY"] or the FINNHUB_KEY env
var. Without it the app still runs on yfinance (fine locally, sparse on cloud).

Every fetch is defensive: market data is messy and endpoints change, so each
function returns a safe empty value rather than raising, and the UI degrades
gracefully when a piece is missing.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
from typing import Any

import pandas as pd
import requests
import yfinance as yf

# SEC requires a descriptive User-Agent with contact info per their fair-access policy.
SEC_HEADERS = {"User-Agent": "stock-analyzer / Follow the Filings (contact: followthefilings@gmail.com)"}
_HTTP_TIMEOUT = 15
_FINNHUB = "https://finnhub.io/api/v1"


def finnhub_key() -> str | None:
    """Resolve the Finnhub key from Streamlit secrets or the environment."""
    try:
        import streamlit as st  # imported lazily so the module works outside Streamlit

        k = st.secrets.get("FINNHUB_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("FINNHUB_KEY") or None


def alphavantage_key() -> str:
    """Alpha Vantage key from secrets/env; falls back to the throttled 'demo' key."""
    try:
        import streamlit as st

        k = st.secrets.get("ALPHAVANTAGE_KEY", "")
        if k:
            return k
    except Exception:
        pass
    return os.environ.get("ALPHAVANTAGE_KEY") or "demo"


def get_top_gainers(limit: int = 15) -> list[str]:
    """Today's biggest gaining US tickers via Alpha Vantage's free movers endpoint.

    Returns a list of clean common-stock tickers (filters out warrants/units).
    Empty list on failure or rate-limit so the caller can degrade gracefully.
    """
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={"function": "TOP_GAINERS_LOSERS", "apikey": alphavantage_key()},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        gainers = (r.json() or {}).get("top_gainers", [])
        out: list[str] = []
        for g in gainers:
            t = (g.get("ticker") or "").upper()
            try:
                price = float(g.get("price", 0) or 0)
            except (TypeError, ValueError):
                price = 0.0
            if not t.isalpha() or len(t) > 5:
                continue
            if len(t) == 5 and t[-1] in ("W", "R", "U"):  # warrant / right / unit
                continue
            if price < 2:  # skip deep penny stocks that dominate day-gainer lists
                continue
            out.append(t)
            if len(out) >= limit:
                break
        return out
    except Exception:
        return []


def _num(v: Any) -> float | None:
    """Coerce to float, treating Finnhub's None/empty/0-as-missing gracefully."""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _frac(v: Any) -> float | None:
    """Finnhub returns percentages (e.g. 27 for 27%); yfinance uses fractions (0.27).

    The scorer is written against yfinance conventions, so convert percent -> fraction.
    """
    n = _num(v)
    return n / 100 if n is not None else None


# --------------------------------------------------------------------------- #
# Core ticker data
# --------------------------------------------------------------------------- #
def get_info(ticker: str) -> dict[str, Any]:
    """Fundamental + descriptive fields, normalized to yfinance `.info` key names.

    Tries Finnhub first (works on cloud); falls back to yfinance (works locally).
    """
    key = finnhub_key()
    if key:
        info = _finnhub_info(ticker, key)
        if info.get("currentPrice"):  # got real data from Finnhub
            return info
    return _yf_info(ticker)


def _yf_info(ticker: str) -> dict[str, Any]:
    try:
        info = yf.Ticker(ticker).info or {}
        # yfinance returns dividendYield as a percentage (e.g. 0.48 = 0.48%);
        # normalize to a fraction so it matches the Finnhub path and fmt_pct().
        dy = info.get("dividendYield")
        if dy is not None and dy > 0:
            info["dividendYield"] = dy / 100
        return info
    except Exception:
        return {}


def _finnhub_info(ticker: str, key: str) -> dict[str, Any]:
    """Build a yfinance-shaped `.info` dict from Finnhub's free endpoints."""
    ticker = ticker.upper()

    def _get(path: str, **params) -> dict:
        try:
            r = requests.get(f"{_FINNHUB}/{path}", params={**params, "token": key}, timeout=_HTTP_TIMEOUT)
            r.raise_for_status()
            return r.json() or {}
        except Exception:
            return {}

    profile = _get("stock/profile2", symbol=ticker)
    quote = _get("quote", symbol=ticker)
    metric = (_get("stock/metric", symbol=ticker, metric="all") or {}).get("metric", {}) or {}

    price = _num(quote.get("c"))
    pe = _num(metric.get("peTTM")) or _num(metric.get("peBasicExclExtraTTM")) or _num(metric.get("peNormalizedAnnual"))
    eps_growth = _frac(metric.get("epsGrowthTTMYoy"))
    peg = None
    if pe and eps_growth and eps_growth > 0:
        peg = round(pe / (eps_growth * 100), 2)  # PEG = P/E ÷ growth%

    de = _num(metric.get("totalDebt/totalEquityAnnual")) or _num(metric.get("longTermDebt/equityAnnual"))
    if de is not None:
        de *= 100  # Finnhub gives a ratio (1.5); yfinance/scorer expect 150

    return {
        "longName": profile.get("name") or ticker,
        "shortName": profile.get("name") or ticker,
        "sector": profile.get("finnhubIndustry") or "—",
        "currentPrice": price,
        "regularMarketPrice": price,
        # Today's move: Finnhub quote gives d (change/share) and dp (% change).
        "regularMarketChange": _num(quote.get("d")),
        "regularMarketChangePercent": _num(quote.get("dp")),
        "previousClose": _num(quote.get("pc")),
        "trailingPE": pe,
        "pegRatio": peg,
        "priceToSalesTrailing12Months": _num(metric.get("psTTM")),
        "priceToBook": _num(metric.get("pbAnnual")) or _num(metric.get("pbQuarterly")),
        "revenueGrowth": _frac(metric.get("revenueGrowthTTMYoy")),
        "earningsGrowth": eps_growth,
        "profitMargins": _frac(metric.get("netProfitMarginTTM")),
        "returnOnEquity": _frac(metric.get("roeTTM")),
        "debtToEquity": de,
        "currentRatio": _num(metric.get("currentRatioAnnual")) or _num(metric.get("currentRatioQuarterly")),
        "quickRatio": _num(metric.get("quickRatioAnnual")),
        "freeCashflow": _num(metric.get("freeCashFlowTTM")),
        "fiftyTwoWeekHigh": _num(metric.get("52WeekHigh")),
        "fiftyTwoWeekLow": _num(metric.get("52WeekLow")),
        # Additional fundamentals/market stats surfaced in the Key Statistics panel.
        "marketCap": (_num(profile.get("marketCapitalization")) or 0) * 1e6 or None,  # profile is in $M
        "beta": _num(metric.get("beta")),
        "dividendYield": _frac(metric.get("currentDividendYieldTTM")) or _frac(metric.get("dividendYieldIndicatedAnnual")),
        "grossMargins": _frac(metric.get("grossMarginTTM")),
        "operatingMargins": _frac(metric.get("operatingMarginTTM")),
        "returnOnAssets": _frac(metric.get("roaTTM")),
        "trailingEps": _num(metric.get("epsTTM")),
        # Finnhub reports avg volume in millions of shares; ×1e6 to match yfinance's raw count.
        "averageVolume": (_num(metric.get("10DayAverageTradingVolume")) or 0) * 1e6 or None,
        "_ret5d": _num(metric.get("5DayPriceReturnDaily")),
        "_ret13w": _num(metric.get("13WeekPriceReturnDaily")),
        "_retytd": _num(metric.get("yearToDatePriceReturnDaily")),
        "_ret52w": _num(metric.get("52WeekPriceReturnDaily")),
        # Dollar price targets are premium-gated on Finnhub's free tier; left None
        # on cloud (yfinance fills it locally). Analyst view comes from
        # get_analyst_consensus() instead, which uses a free Finnhub endpoint.
        "targetMeanPrice": None,
        # 6-month price return (%) — lets the momentum signal work without a price series.
        "_mom6m": _num(metric.get("26WeekPriceReturnDaily")),
        "_source": "finnhub",
    }


def get_price_history(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Daily price history via yfinance (works locally).

    On cloud Yahoo blocks the server IP, so this is often empty there; the
    momentum signal is sourced from Finnhub metrics instead (see analysis.py),
    and the price chart simply hides when no series is available.
    """
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
    """Recent insider (officers/directors) buys and sells, from Form 4 data.

    Uses Finnhub's free insider-transactions endpoint when a key is present
    (works on cloud); otherwise falls back to yfinance.
    """
    key = finnhub_key()
    if key:
        df = _finnhub_insiders(ticker, key)
        if not df.empty:
            return df
    try:
        df = yf.Ticker(ticker).insider_transactions
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _finnhub_insiders(ticker: str, key: str) -> pd.DataFrame:
    """Shape Finnhub insider data into columns the scorer + UI understand."""
    try:
        r = requests.get(
            f"{_FINNHUB}/stock/insider-transactions",
            params={"symbol": ticker.upper(), "token": key},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        rows = (r.json() or {}).get("data", [])
        if not rows:
            return pd.DataFrame()
        # SEC Form 4 codes: P = open-market purchase, S = sale.
        code_map = {"P": "Purchase", "S": "Sale", "A": "Grant/Award", "M": "Option exercise"}
        out = []
        for d in rows[:25]:
            code = (d.get("transactionCode") or "").upper()
            out.append(
                {
                    "Insider": d.get("name", ""),
                    "Shares": d.get("change", 0),
                    "Transaction": code_map.get(code, code or "—"),
                    "Date": d.get("transactionDate", ""),
                }
            )
        return pd.DataFrame(out)
    except Exception:
        return pd.DataFrame()


def get_recommendations(ticker: str) -> pd.DataFrame:
    """Analyst recommendation trend."""
    try:
        df = yf.Ticker(ticker).recommendations
        return df if isinstance(df, pd.DataFrame) else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _rating_label(score: float) -> str:
    """Map a 1-5 consensus score (5 = most bullish) to a label."""
    if score >= 4.5:
        return "Strong Buy"
    if score >= 3.5:
        return "Buy"
    if score >= 2.5:
        return "Hold"
    if score >= 1.5:
        return "Sell"
    return "Strong Sell"


def get_analyst_consensus(ticker: str) -> dict[str, Any] | None:
    """Analyst Buy/Hold/Sell consensus.

    Uses Finnhub's free recommendation-trends endpoint (works on cloud) when a
    key is present; falls back to yfinance's recommendation fields locally.
    Returns {rating, score, bullish_pct, total, counts, period} or None.
    """
    key = finnhub_key()
    if key:
        c = _finnhub_consensus(ticker, key)
        if c:
            return c
    return _yf_consensus(ticker)


def _finnhub_consensus(ticker: str, key: str) -> dict[str, Any] | None:
    try:
        r = requests.get(
            f"{_FINNHUB}/stock/recommendation",
            params={"symbol": ticker.upper(), "token": key},
            timeout=_HTTP_TIMEOUT,
        )
        r.raise_for_status()
        rows = r.json() or []
        if not rows:
            return None
        d = rows[0]  # most recent month
        sb, b, h, s, ss = (
            int(d.get("strongBuy", 0)),
            int(d.get("buy", 0)),
            int(d.get("hold", 0)),
            int(d.get("sell", 0)),
            int(d.get("strongSell", 0)),
        )
        total = sb + b + h + s + ss
        if total == 0:
            return None
        score = (sb * 5 + b * 4 + h * 3 + s * 2 + ss * 1) / total
        return {
            "rating": _rating_label(score),
            "score": round(score, 2),
            "bullish_pct": round((sb + b) / total * 100),
            "total": total,
            "counts": {"Strong Buy": sb, "Buy": b, "Hold": h, "Sell": s, "Strong Sell": ss},
            "period": d.get("period"),
        }
    except Exception:
        return None


def _yf_consensus(ticker: str) -> dict[str, Any] | None:
    """Local fallback from yfinance .info recommendation fields."""
    try:
        info = yf.Ticker(ticker).info or {}
        mean = info.get("recommendationMean")  # Yahoo: 1 = Strong Buy ... 5 = Sell (inverted vs ours)
        n = info.get("numberOfAnalystOpinions")
        key_ = info.get("recommendationKey")
        if mean is None and not key_:
            return None
        if mean is not None:
            score = 6 - float(mean)  # flip to our scale where higher = more bullish
            rating = _rating_label(score)
        else:
            rating = str(key_).replace("_", " ").title()
            score = None
        return {"rating": rating, "score": round(score, 2) if score is not None else None,
                "bullish_pct": None, "total": n, "counts": None, "period": None}
    except Exception:
        return None


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


# --------------------------------------------------------------------------- #
# Congressional (US House) stock trades — parsed from the official Clerk filings
# (free, no key, not IP-blocked). Members file Periodic Transaction Reports (PTRs)
# under the STOCK Act; we read the year's bulk index then parse the typed PDFs.
# --------------------------------------------------------------------------- #
_HOUSE = "https://disclosures-clerk.house.gov/public_disc"
_TXN_LABELS = {"P": "Buy", "S": "Sell", "E": "Exchange"}
# Holding row in a PTR: "<asset> (TICKER) [ST] <type> <txdate><notifdate><amount>".
_PTR_ROW = re.compile(
    r"\(([A-Z]{1,5})\)\s*\[ST\]\s*([PSE])(\s*\(partial\))?\s*"
    r"(\d{2}/\d{2}/\d{4})\d{2}/\d{2}/\d{4}(\$[\d,]+\s*-\s*\$[\d,]+)"
)


def _house_ptr_index(year: int) -> list[dict[str, Any]]:
    """Return the year's PTR (stock-trade) filings from the House bulk index."""
    import io
    import zipfile

    try:
        r = requests.get(f"{_HOUSE}/financial-pdfs/{year}FD.ZIP", headers=SEC_HEADERS, timeout=_HTTP_TIMEOUT)
        r.raise_for_status()
        xml = zipfile.ZipFile(io.BytesIO(r.content)).read(f"{year}FD.xml").decode("utf-8", "ignore")
    except Exception:
        return []
    out = []
    for rec in re.findall(r"<Member>(.*?)</Member>", xml, re.S):
        if "<FilingType>P</FilingType>" not in rec:
            continue

        def field(tag: str) -> str:
            m = re.search(fr"<{tag}>(.*?)</{tag}>", rec)
            return m.group(1).strip() if m else ""

        try:
            d = _dt.datetime.strptime(field("FilingDate"), "%m/%d/%Y")
        except ValueError:
            d = _dt.datetime.min
        out.append(
            {
                "name": f"{field('First')} {field('Last')}".strip(),
                "state": field("StateDst"),
                "docid": field("DocID"),
                "date": d,
                "year": year,
            }
        )
    return out


def _parse_house_ptr(year: int, docid: str) -> list[dict[str, str]]:
    """Extract individual trades from one PTR PDF. Empty if scanned/unparseable."""
    import io

    try:
        from pypdf import PdfReader

        r = requests.get(f"{_HOUSE}/ptr-pdfs/{year}/{docid}.pdf", headers=SEC_HEADERS, timeout=_HTTP_TIMEOUT)
        if r.status_code != 200 or r.content[:4] != b"%PDF":
            return []
        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(r.content)).pages)
    except Exception:
        return []
    trades = []
    for ticker, typ, partial, date, amount in _PTR_ROW.findall(text):
        label = _TXN_LABELS.get(typ, typ) + (" (partial)" if partial else "")
        trades.append(
            {
                "ticker": ticker,
                "type": label,
                "date": date,
                "amount": re.sub(r"\s+", " ", amount).strip(),
                "doc_url": f"{_HOUSE}/ptr-pdfs/{year}/{docid}.pdf",
            }
        )
    return trades


def get_congress_trades(max_reports: int = 25, ticker: str | None = None) -> list[dict[str, Any]]:
    """Recent US House stock trades from official Clerk PTR filings.

    Parses the most recent `max_reports` filings (each may contain several
    trades). House (Representatives) only — the Senate uses a separate
    anti-bot system. Data has a reporting lag and amounts are disclosed as
    ranges, per the STOCK Act.
    """
    year = _dt.date.today().year
    index = _house_ptr_index(year) or _house_ptr_index(year - 1)
    index.sort(key=lambda r: r["date"], reverse=True)
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for rec in index[:max_reports]:
        for t in _parse_house_ptr(rec["year"], rec["docid"]):
            # Dedupe exact repeats (some PDFs list a holding twice when extracted).
            key = (rec["docid"], t["ticker"], t["type"], t["date"], t["amount"])
            if key in seen:
                continue
            seen.add(key)
            t.update({"member": rec["name"], "state": rec["state"], "filed": rec["date"].strftime("%m/%d/%Y")})
            rows.append(t)
    if ticker:
        rows = [r for r in rows if r["ticker"] == ticker.upper()]
    return rows


# --------------------------------------------------------------------------- #
# Senate stock trades — from the official Senate eFD system (free). Requires a
# CSRF + agreement handshake, then the report search returns JSON; electronic
# PTRs render a transactions table we parse with pandas.
# --------------------------------------------------------------------------- #
_SENATE = "https://efdsearch.senate.gov"
_BROWSER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"}


def _normalize_txn(raw: str) -> str:
    raw = (raw or "").strip()
    if "Purchase" in raw:
        return "Buy"
    if "Sale" in raw:
        return "Sell (partial)" if "Partial" in raw else "Sell"
    if "Exchange" in raw:
        return "Exchange"
    return raw


def get_senate_trades(max_reports: int = 20, ticker: str | None = None) -> list[dict[str, Any]]:
    """Recent US Senate stock trades from the official Senate eFD system."""
    import io

    import pandas as pd

    rows: list[dict[str, Any]] = []
    try:
        s = requests.Session()
        s.headers.update(_BROWSER_UA)
        landing = s.get(f"{_SENATE}/search/", timeout=_HTTP_TIMEOUT)
        m = re.search(r'name="csrfmiddlewaretoken"\s+value="([^"]+)"', landing.text)
        csrf = m.group(1) if m else s.cookies.get("csrftoken")
        s.post(f"{_SENATE}/search/home/", data={"csrfmiddlewaretoken": csrf, "prohibition_agreement": "1"},
               headers={"Referer": f"{_SENATE}/search/"}, timeout=_HTTP_TIMEOUT)
        csrf = s.cookies.get("csrftoken") or csrf
        year = _dt.date.today().year
        payload = {"csrfmiddlewaretoken": csrf, "start": "0", "length": str(max_reports),
                   "report_types": "[11]", "filer_types": "[]",
                   "submitted_start_date": f"01/01/{year} 00:00:00", "submitted_end_date": "",
                   "draw": "1", "order[0][column]": "4", "order[0][dir]": "desc"}
        for i in range(5):
            payload[f"columns[{i}][data]"] = str(i)
        data = s.post(f"{_SENATE}/search/report/data/", data=payload,
                      headers={"Referer": f"{_SENATE}/search/", "X-Requested-With": "XMLHttpRequest"},
                      timeout=_HTTP_TIMEOUT).json()
        for row in data.get("data", [])[:max_reports]:
            href = re.search(r'href="([^"]+)"', row[3])
            if not href or "/ptr/" not in href.group(1):  # skip scanned/paper filings
                continue
            member = f"{row[0]} {row[1]}".strip().rstrip(",").strip()
            filed = row[4]
            try:
                pg = s.get(_SENATE + href.group(1), headers={"Referer": f"{_SENATE}/search/"}, timeout=_HTTP_TIMEOUT)
                tbl = pd.read_html(io.StringIO(pg.text))[0]
            except Exception:
                continue
            for _, tr in tbl.iterrows():
                tk = str(tr.get("Ticker", "")).strip().upper()
                if not tk or tk in ("--", "NAN", "") or not tk.replace(".", "").isalnum():
                    continue
                rows.append({"member": member, "filed": filed, "ticker": tk,
                             "type": _normalize_txn(str(tr.get("Type", ""))),
                             "amount": str(tr.get("Amount", "")).strip(),
                             "date": str(tr.get("Transaction Date", "")).strip(),
                             "doc_url": _SENATE + href.group(1)})
    except Exception:
        pass
    if ticker:
        rows = [r for r in rows if r["ticker"] == ticker.upper()]
    return rows


# --------------------------------------------------------------------------- #
# Big-investor 13F holdings — what famous funds own, from free SEC EDGAR data.
# 13F values are reported in whole dollars (post-2023 SEC rule). ~45-day lag.
# --------------------------------------------------------------------------- #
FAMOUS_FUNDS = {
    "Berkshire Hathaway — Warren Buffett": "0001067983",
    "Scion Asset Mgmt — Michael Burry": "0001649339",
    "Pershing Square — Bill Ackman": "0001336528",
    "Bridgewater — Ray Dalio": "0001350694",
    "Renaissance Technologies": "0001037389",
    "Appaloosa — David Tepper": "0001656456",
    "Third Point — Dan Loeb": "0001040273",
}


def get_13f_holdings(cik: str, top: int = 15) -> dict[str, Any]:
    """Latest 13F-HR holdings for a fund CIK, aggregated by issuer and sorted by value."""
    cik = cik.zfill(10)
    try:
        sub = requests.get(f"https://data.sec.gov/submissions/CIK{cik}.json", headers=SEC_HEADERS, timeout=_HTTP_TIMEOUT).json()
        rec = sub["filings"]["recent"]
        idx = next((i for i, f in enumerate(rec["form"]) if f == "13F-HR"), None)
        if idx is None:
            return {"filed": None, "holdings": []}
        acc = rec["accessionNumber"][idx].replace("-", "")
        filed = rec["filingDate"][idx]
        folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc}"
        fi = requests.get(f"{folder}/index.json", headers=SEC_HEADERS, timeout=_HTTP_TIMEOUT).json()
        names = [it["name"] for it in fi["directory"]["item"]]
        cand = [n for n in names if n.lower().endswith(".xml") and "primary" not in n.lower()]
        if not cand:
            return {"filed": filed, "holdings": []}
        xml = requests.get(f"{folder}/{cand[0]}", headers=SEC_HEADERS, timeout=_HTTP_TIMEOUT).text
        value: dict[str, int] = {}
        shares: dict[str, int] = {}
        for blk in re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", xml, re.S):
            nm = re.search(r"<(?:\w+:)?nameOfIssuer>(.*?)<", blk)
            vl = re.search(r"<(?:\w+:)?value>(.*?)<", blk)
            sh = re.search(r"<(?:\w+:)?sshPrnamt>(.*?)<", blk)
            if nm and vl:
                import html as _html

                n = _html.unescape(nm.group(1).strip()).title()
                value[n] = value.get(n, 0) + int(vl.group(1))
                if sh:
                    shares[n] = shares.get(n, 0) + int(sh.group(1))
        total = sum(value.values()) or 1
        holdings = [{"issuer": n, "value": v, "pct": v / total * 100, "shares": shares.get(n, 0)}
                    for n, v in value.items()]
        holdings.sort(key=lambda x: -x["value"])
        return {"filed": filed, "holdings": holdings[:top], "total": total,
                "positions": len(value), "url": folder}
    except Exception:
        return {"filed": None, "holdings": []}


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
