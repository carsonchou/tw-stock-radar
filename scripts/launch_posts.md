# 發文清單 — 按順序執行

---

## STEP 0 — awesome-quant PR ping（30 秒）

執行：
```
! gh pr comment 452 --repo wilsonfreitas/awesome-quant --body "Hi @wilsonfreitas 👋 Following up on this PR — the project has been updated since submission: now covers all 1,900+ TWSE + TPEX stocks, CI is green on Python 3.9 & 3.11, and the README has been significantly expanded. Happy to make any changes needed to fit the list's format. Thanks for maintaining awesome-quant!"
```

---

## STEP 1 — Reddit r/algotrading

URL：https://www.reddit.com/r/algotrading/submit

**Title（直接貼）：**
```
I built a free Taiwan stock scanner — institutional chips + 13 technicals, 1900+ stocks, no paid API [OC]
```

**Body（直接貼）：**
```
Been trading Taiwan stocks and frustrated by the lack of good free tools. Built tw-stock-radar: scans all 1,900+ TWSE/TPEX stocks daily and shows what institutions are doing.

**The interesting part — Taiwan's chips data is unusually free:**
Taiwan's stock exchange (TWSE) publishes daily net buy/sell for every foreign fund, trust fund, and dealer. TDCC (the depository) releases weekly ownership distribution across 16 tiers — you can literally see how many retail investors (holding 1–10 lots) exited a stock this week. All of this is on government open-data portals, free, no sign-up.

The scanner wires it all together:
- Scores every stock 0–100 across 4 dimensions (trend/position/momentum/volatility)
- 13 indicators per stock: RSI, SuperTrend, MACD, ADX, %B, ATR, OBV, DMI, Williams %R, CCI, Renko
- ATR Chandelier stop-loss + TP1 (+1.5R) + TP2 (+4.5R) signals
- Push alerts via ntfy when signals confirm at EOD
- Dark HUD dashboard with sector heatmap, chips flow rankings, real track record

**Honest disclaimer:** Technical signals alone show ~50% win rate on the full Taiwan universe — no free lunch in isolation. The value is multi-source confluence (technicals + institutional accumulation + retail exit at the same time), and having all of this in one view instead of five separate government portals.

GitHub: https://github.com/carsonchou/tw-stock-radar
100% free open data, MIT license, ~110 unit tests, no API key needed for core features.
```

---

## STEP 2 — Reddit r/Python

URL：https://www.reddit.com/r/Python/submit

**Title：**
```
I built a stock market scanner with zero framework overhead — stdlib http.server, single HTML file, 7 deps, 109 tests in 0.2s [OC]
```

**Body：**
```
Side project that got out of hand: a full Taiwan stock market scanner (1,900+ stocks) that I wanted to keep as dependency-light as possible.

**The constraint I set myself:** no web framework, no bundler, no build step.

Result:
- Backend: stdlib `http.server.ThreadingHTTPServer` + SSE for live updates
- Frontend: one self-contained `dashboard.html` (vanilla JS + three.js inlined)
- Test suite: stdlib `unittest` only — no pytest, no mocks, zero network calls, 109 tests in 0.2s
- Runtime deps: 7 (yfinance, pandas, numpy, twstock, openai, python-dotenv, requests)

**A few engineering bits that came out of the constraint:**

The chips pipeline hits three separate Taiwan government open-data APIs (TWSE T86, TWSE MI_MARGN, TDCC), each returning full-market data in a single GET — so no per-stock rate-limiting.

The backtester splits train/test by odd/even bar index, not by date range, to avoid look-ahead bias regardless of uneven bar counts across 1,900 stocks.

The "track record" tab uses real cached prices at T+5/T+10 to evaluate past signals — not replayed backtest parameters. Keeps it honest.

GitHub: https://github.com/carsonchou/tw-stock-radar
MIT license. Taiwan-specific but the architecture is generic.
```

---

## STEP 3 — Hacker News Show HN

URL：https://news.ycombinator.com/submit

**Title（字數有限，就這樣貼）：**
```
Show HN: Free Taiwan stock scanner – institutional chips + 13 indicators, 1900+ stocks, no API key
```

**URL：**
```
https://github.com/carsonchou/tw-stock-radar
```

**第一則留言（提交後馬上自己回）：**
```
Hey HN. Built this out of frustration — Taiwan has surprisingly good free institutional data (daily net buy/sell for every foreign fund and trust fund, weekly retail ownership distribution across 16 tiers from TDCC), but it's scattered across five government portals in different formats.

This scanner wires them together: price history + chips flow + fundamentals, scanned daily across all 1,900+ listed stocks, with a dark HUD dashboard.

A few things I thought were interesting to build:
- Used stdlib http.server + SSE instead of FastAPI/Flask — works fine for a single-user local tool, and the dependency count stays at 7
- The test suite is stdlib unittest only, zero network calls, runs in 0.2s — wanted something I could run in CI without any setup overhead
- "Track record" tab shows real post-signal outcomes from live prices, not backtested parameters
- Backtester splits train/test by odd/even bar index to avoid look-ahead bias regardless of uneven bar counts per stock

Honest disclosure: technical signals alone show ~50% win rate across the full Taiwan universe. The tool's value is information aggregation and chips confluence, not alpha generation by itself.

Happy to discuss the Taiwan market data landscape — it's surprisingly accessible for a non-US market.
```

**⏰ 發文時機：週二或週三，台灣時間晚上 9–11 點（美東早上 9–11am）**

---

## STEP 4 — PTT Soft_Job

URL：https://www.ptt.cc/bbs/Soft_Job/

**標題：**
```
[心得] 做了台股全市場掃描器，開源免費，歡迎一起維護
```

**內文：**
```
分享個自己每天用的工具，掃台股上市櫃 1900+ 支股票，
整合技術面 + 三大法人/集保籌碼 + 基本面，有買賣訊號和停損停利。

功能：
- 13 個技術指標（RSI/SuperTrend/MACD/ADX/%B/ATR 等），每支股票 0~100 分
- 三大法人 T86/融資券當沖/集保籌碼（全部免費 open data，不用帳號）
- 集保 16 級散戶持股週變化（散戶流出 + 法人連買 = 主力吸籌訊號）
- ATR Chandelier 停損 + TP1(+1.5R) + TP2(+4.5R)
- 深色 HUD 看板（個股深度頁有基本面財報 + 四師解盤）

技術面：
- stdlib http.server，沒用 FastAPI/Flask
- 單一 HTML 檔，沒有 build step
- 109 個不連網 unit test，0.2 秒跑完
- 7 個 runtime 依賴

誠實說明：技術訊號單獨跑全市場約 50% 勝率，沒有 edge。
真正有用的是「技術 + 法人 + 集保多維佐證」一頁全看到，
省去自己去五個政府網站拼資料的時間。

GitHub：https://github.com/carsonchou/tw-stock-radar
MIT 授權，歡迎 PR。
```

---

## 執行順序建議

| 順序 | 動作 | 預期效果 |
|------|------|---------|
| 現在 | 找 5 個朋友按星星（傳連結過去） | 先到 10+ 顆 |
| 今天 | STEP 0 — PR ping | 加速 awesome-quant merge |
| 今天 | STEP 1 — r/algotrading | 最對口的受眾 |
| 明天 | STEP 2 — r/Python | 工程師受眾 |
| 週二~三晚上 9–11 點 | STEP 3 — HN Show HN | 最高上限 |
| 任何時間 | STEP 4 — PTT | 台灣受眾 |
