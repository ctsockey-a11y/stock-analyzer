# 📈 Stock Holdings Analyzer

A free, mobile-friendly equity research dashboard. Analyze your portfolio, see
**why** a stock scores well or poorly, track what institutions & insiders are
buying, read the latest news and SEC filings, and screen for high-potential
stocks — all from data sources that cost nothing.

- **Data:** [yfinance] (prices, fundamentals, holders, insiders) + [SEC EDGAR]
  (filings) — no API keys needed. Optional free [Finnhub] key adds richer news.
- **Hosting:** runs free on [Streamlit Community Cloud] → reachable from any
  device (phone, tablet, laptop) at a `*.streamlit.app` URL.

> ⚠️ **Educational tool, not financial advice.** Scores are rule-based heuristics
> on delayed/imperfect data. Always do your own research.

---

## What it does

| Tab | What you get |
|-----|--------------|
| 💼 **My Portfolio** | Total value, weighted health score, concentration & sector-risk warnings, allocation and per-holding health charts. |
| 🔬 **Analyze a Stock** | 0–100 score across 5 pillars (Valuation, Growth, Profitability, Financial health, Smart-money & momentum), plain-English strengths/risks, 1-yr price chart, institutional holders, insider (Form 4) trades, news, and latest SEC filings. |
| 🚀 **Opportunity Screener** | Ranks any list of tickers by an **opportunity score** tilted toward growth + smart-money conviction at a reasonable valuation. |

How the scoring works is fully transparent — see `analyzer/analysis.py`. Each
pillar lists exactly which metrics moved the score and why.

## Run it locally

```bash
cd stock-analyzer
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501.

## Deploy it free (reachable from any device)

1. **Put it on GitHub** (free account at github.com):
   ```bash
   cd stock-analyzer
   git init && git add . && git commit -m "Stock analyzer"
   gh repo create stock-analyzer --public --source=. --push
   # or create the repo on github.com and `git push` to it
   ```
2. **Deploy on Streamlit Community Cloud** (free):
   - Go to https://share.streamlit.io and sign in with GitHub.
   - Click **New app**, pick your `stock-analyzer` repo, set the main file to
     `app.py`, and click **Deploy**.
   - In ~2 minutes you get a public URL like
     `https://your-name-stock-analyzer.streamlit.app` — bookmark it on your phone.

The `secrets.toml` file is git-ignored and never uploaded. To use a Finnhub
news key in the cloud, paste it under **App → Settings → Secrets**:
```toml
FINNHUB_KEY = "your_key_here"
```

## Optional: free news API key (Finnhub)

News works out of the box with free Yahoo headlines. For more headlines:
1. Register free at https://finnhub.io/register and copy your key.
2. Locally: `cp .streamlit/secrets.toml.example .streamlit/secrets.toml` and fill it in.
3. In the cloud: paste it into the app's **Secrets** (see above).

## Your holdings

Use the sidebar to load a **sample portfolio**, **upload a CSV**
(`ticker,shares,cost_basis`), or **type positions manually**. Nothing is stored
on a server — your holdings live only in your browser session.

[yfinance]: https://github.com/ranaroussi/yfinance
[SEC EDGAR]: https://www.sec.gov/edgar
[Finnhub]: https://finnhub.io/
[Streamlit Community Cloud]: https://share.streamlit.io/
