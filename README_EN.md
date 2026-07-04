# tw-stock-radar 🎯

![Python](https://img.shields.io/badge/Python-3.9+-blue?logo=python&logoColor=white)
![Install](https://img.shields.io/badge/install-pip%20from%20GitHub-blue)
![License](https://img.shields.io/badge/License-MIT-green)
![Tests](https://img.shields.io/badge/Tests-109%20passing-brightgreen)
![Data](https://img.shields.io/badge/Data-100%25%20free%20open%20data-orange)
![Tests](https://github.com/carsonchou/tw-stock-radar/actions/workflows/test.yml/badge.svg)

**Free Python scanner for all 1,800+ TWSE/TPEX stocks** — institutional chips, 13 technicals, fundamentals, and AI analyst deep-dives in a dark HUD dashboard. No API key required for core features.

> Scans all 1,800+ TWSE + TPEX listed stocks. Used daily in production.

[繁體中文 README](README.md)

---

## Screenshots

![Dashboard Live — market temperature, sector rotation, signal cards](docs/demo-radar.gif)

![Market Radar — temperature gauge, sector rotation heatmap, signal cards](docs/screenshot-dashboard.png)
*Market temperature 54.6 (neutral), sector rotation heatmap, live buy/sell signal cards with stop-loss and TP levels*

![Institutional Chips — foreign & trust net buy rankings, consecutive buy streaks](docs/screenshot-chips.png)
*T-1 institutional data: TWSE T86 foreign/trust/dealer net buy rankings, consecutive buy streak counter*

![Track Record — real win rate and R-multiple from live signals](docs/screenshot-sector.png)
*Real post-signal tracking: actual entry→exit prices, P&L%, and R-multiple from live data — not backtest*

---

## Features

### 📡 Full-Market Scanner
- Scores every stock **0–100** across 4 orthogonal dimensions: trend / position / momentum / volatility
- **13 indicators** per stock: RSI, MA20, SuperTrend, MACD, ADX, %B, ATR, OBV, DMI, Williams %R, CCI, Renko candles, and more
- Detects buy/sell signals with ATR Chandelier stop-loss, TP1 (+1.5R), TP2 (+4.5R)
- Push alerts via [ntfy](https://ntfy.sh) when new signals confirm at end of day (deduplicated)

### 🏦 Chips Module (Free Open Data)

| Source | Data |
|--------|------|
| TWSE T86 | 3 major institutional players net buy/sell + consecutive buy streak |
| Margin data | Margin balance change, short ratio, day-trading ratio |
| TDCC (集保) | 16-tier retail shareholding weekly change |

Retail outflow + institutional accumulation = classic smart money setup.

### 📊 Dark HUD Dashboard (5 tabs)
- **Radar**: market temperature gauge, animated three.js reactor orb, sector heat flow, live signal cards with stop/TP levels
- **Sectors**: capital flow treemap (area = number of stocks, color = return)
- **Chips Flow**: institutional net buy rankings, margin hot list, TDCC retail-exit leaderboard
- **Track Record**: real win rate + average R from live signals (not backtest)
- **History**: intraday signal timeline

### 🔍 Deep Stock Page
- Real-time 5-level order book (TWSE MIS, ~20s delay) + intraday 1-min chart
- Health scorecard: A–E grade across 4 dimensions (technicals / chips / fundamentals / valuation)
- Candlestick with MA, weekly/monthly views
- Financials: EPS (TTM + quarterly), revenue YoY/MoM, margins, P/E, P/B, dividend yield, ROE
- **Four AI teachers**: per-stock deep-dive in 4 methodologies — trend following, chips reading, warrant flow, swing trading — with entry zone and step-by-step playbook
- Google News RSS + watchlist + price alerts (localStorage, live refresh)

---

## Quick Start

**Option A — pip install from GitHub (no PyPI required):**
```bash
pip install git+https://github.com/carsonchou/tw-stock-radar
tw-stock-radar          # → http://localhost:8899
```

**Option B — from source:**
```bash
git clone https://github.com/carsonchou/tw-stock-radar
cd tw-stock-radar
pip install -r requirements.txt
cp .env.example .env
python app.py           # → http://localhost:8899
```

End-of-day full pipeline (chips → scan → notify):
```bash
python eod.py
```

---

## Data Sources

All free, no sign-up required for core scanner + dashboard:

| Source | Data |
|--------|------|
| TWSE / TPEX open data | Price, volume, institutional net buy (T86) |
| TDCC (集保) public disclosure | 16-tier shareholding distribution |
| yfinance | Price history, fundamentals |
| twstock | Stock list, industry classification |
| Google News RSS | Per-stock news |

Optional (`.env`):
- `OPENAI_API_KEY` — enables the Four AI Teachers panel (works with OpenRouter free models)
- `FINMIND_TOKEN` — richer financial statements (free tier available at finmindtrade.com)
- `NTFY_TOPIC` — push alerts to your phone via [ntfy.sh](https://ntfy.sh)

---

## Testing

```bash
python -m unittest discover -s tests/ -v
```

~100 tests, stdlib unittest only, zero network calls, passes in < 3 seconds.

---

## License

MIT
