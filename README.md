# 量化阿森 · 台股數據獵手 🎯

> 🇺🇸 English README: [README_EN.md](README_EN.md)

一支 **AI 自動掃台股市場強弱 + 三大法人/融資券/集保籌碼、推做多做空警報** 的深色 HUD 儀表板 + 背景機器人 + 一鍵內容產生器。靈感來自 IG「ELITE 數據獵手」，但做成 **真能跑、真實 K 線/籌碼計算、經回測驗證** 的台股版。你自己看盤用，畫面同時就是 IG/YT 引流素材。

> **誠實定位（重要）**：技術訊號（SuperTrend 翻多等）經自寫回測驗證，**對台股全市場無獨立可交易 edge**（單純套策略只 ~50% 獲利檔，與「無腦套非通用」結論一致）。本工具的真正價值是：① **資訊彙整**（強弱/板塊/三大法人/融資券/當沖/集保一頁看穿）② **經驗證有預測力的強弱分**（分位對未來5日報酬單調遞增）③ **籌碼 confluence**（技術+法人+集保多維佐證）④ **內容素材化**（一鍵產 IG/YT 貼文）。訊號當「技術觸發點+多維佐證」看，**非保證獲利**。本看板僅技術數據呈現，非投資建議。

## 它做什麼

每輪掃描：抓台股日線（yfinance 即時優先／退 `twdata/cache`）→ 每檔算 RSI/MA/SuperTrend/MACD/ADX/%B/ATR → **強弱分 0–100（正交四維：趨勢/位置/動能/波動）** → 合併**三大法人+融資券+當沖+集保**籌碼 → 聚合成看板 → 偵測做多做空訊號（附 ATR 停損+tp1/tp2）→ 寫 `state.json` → 新訊號推 ntfy（收盤確認、當日去重）。

**看板五分頁（深色鋼鐵人 HUD）**
- **看板**：市場溫度 gauge（多成分：RSI+站上20MA+漲跌家數+新高新低+量能）、three.js 溫度反應爐、產業板塊熱流、強弱榜、做多做空訊號卡（附停損停利）、做多空白用觀察名單填、JARVIS 逐字播報、漲跌家數紅綠廣度條、大盤總閘徽章
- **板塊熱流**：資金流向 treemap（面積=家數、紅漲綠跌）
- **法人資金流向**：外資/投信買超榜 + 連買榜 + 融資融券/當沖熱榜 + 集保散戶流出（主力吸籌）榜
- **實盤戰績**：已推訊號的真實勝率/平均R/成績單（track.py 用快取價事後評估，非回測美化）
- **歷史訊號**：當日訊號時間軸
- **自選股條**（★ 加入的個股快速報價）、**多元排行**（漲幅/跌幅/成交值/振幅）

點任一個股 → popover 迷你日K；**搜尋代號/名稱 → 個股深度頁（對齊三竹個股頁）**：

## 個股深度頁（搜尋任一上市櫃/ETF）
機構級金融終端風格（IBM Plex 字體、去發光、紅漲綠跌）。由上而下：
- **即時五檔 ORDER BOOK**（證交所 MIS，免費約20秒延遲）：委買委賣五檔價量條 + 成交/漲跌 + **今日分時走勢線**（yfinance 1分K）
- **個股健診六宮格**：四面向連續計分（技術/籌碼/**基本面**/**估值**）+ 等級 A–E + 信心度（`health.py`，取代舊拍板式評分）
- **K線 日/週/月** 切換（蠟燭+MA20）
- **13 技術指標**：RSI/MA/SuperTrend/MACD/ADX/%B/ATR + **OBV/DMI/威廉%R/CCI/寶塔線**
- **基本面財報**：EPS(近四季/單季)/年增、營收年增YoY/月增、毛利率/營益率、**PE/PB/殖利率/股利/除權息日**、ROE(推估)
- **四維籌碼**：三大法人/融資券/當沖/集保
- **四師手把手教學**：朱家泓/阿斯匹靈/權證小哥/張捷 四維度深度判讀 + 綜合操作區間（買在哪賣在哪停損目標風報比）+ 逐步操作
- **個股新聞**（Google News RSS）
- 頂部 **★自選股 + 到價提醒**（漲抵/跌破，localStorage，盤中刷新推播）

> 做不到（誠實）：即時逐筆真 tick（MIS 是~20秒快照）、券商分點（TWSE 分點報表有 captcha）——需付費行情源。

## 怎麼用

| 想做的事 | 指令 / 雙擊 |
|---|---|
| **桌面 app 一鍵開（推薦）** | 桌面捷徑「台股數據獵手」或 `app_launch.bat` |
| **盤後一鍵全跑**（刷快取→籌碼→掃描→產貼文） | `python eod.py`（`--no-post` 只到看板／`--push` 推ntfy） |
| **一鍵今日貼文**（IG/YT 文案+海報圖） | `python daily_post.py`（`--scan` 先掃／`--themes board,chips`） |
| 開即時看板 | `開啟看板.bat` |
| 背景掃描迴圈（自動推 ntfy） | `背景掃描.bat` |
| 掃全市場 ~1900 檔 | `全市場掃描.bat` 或 `scan.py --full` |
| 跟市場同步（證交所即時價，盤中秒級） | `scan.py --realtime` |
| 刷快取／只讀快取不連網 | `scan.py --freshen`／`scan.py --cache` |
| 訊號參數校準（研究用，不回寫除非 --apply） | `python calibrate.py [--full]` |
| 跑單元測試（~100 個，全綠） | `python -m unittest discover -s tests` |

看板網址：<http://127.0.0.1:8899/>；快照海報：`?snapshot=1|chips|track|flow`（IG 直式 1080×1350）。

## 籌碼面（皆免費 open data、盤後 T+0、優雅降級、盤中標 T-1）

| 模組 | 來源 | 內容 |
|---|---|---|
| `chips.py` | TWSE **T86** + TPEX | 三大法人買賣超（外資=外陸資+外資自營、投信；排除自營雜訊）、外資+投信連買天數、投信單獨連買（阿斯匹靈法） |
| `margin.py` | TWSE **MI_MARGN** + **exchangeReport/TWTB4U** | 融資餘額/增減、融券、券資比%、當沖比% |
| `tdcc.py` | **TDCC** QryStockAjax | 集保 16 級持股分布、小散戶（持1-10張）戶數週變化 → 散戶流出=主力吸籌 |

一個 GET 抓全市場當日（非逐檔），成本極低；本地快取 `twdata/{chips,margin,tdcc}/`（gitignore）。**籌碼只當顯示/confluence，不硬 gate 砍訊號。**

## 資料來源
- 價量：**twstock 官方日線**（證交所/櫃買，上市櫃皆正確；yfinance 抓台股不可靠已停用日線）、**twstock.realtime**＋**證交所 MIS**（即時撮合價/五檔，盤中約20秒延遲）、yfinance（僅分時1分K）、`twdata/cache/`
- 籌碼：TWSE/TPEX/TDCC open data（見上表）
- **基本面**：證交所 **BWIBBU_ALL**（PE/PB/殖利率，全市場當日、免 token）＋ **FinMind**（EPS/毛利/月營收/股利/除權息；`FINMIND_TOKEN` env 選配，免 token 300/hr）→ `fundamentals.py`
- **新聞**：Google News RSS → `news.py`
- 跑在 `D:\ClawWork\.venv`（pandas/numpy/twstock/yfinance）；推播 `../notify.py`（ntfy topic 從 `../.env`）

## 檔案
```
data_hunter/
├─ universe.py     精選~125檔/18產業 + load_full_universe(全市場~1900/34產業)
├─ scan.py         掃描引擎(指標→強弱分→籌碼合併→訊號→排行→state.json→推播)
├─ chips.py/margin.py/tdcc.py   三大法人 / 融資券當沖 / 集保 籌碼
├─ fundamentals.py 基本面/估值(BWIBBU + FinMind：EPS/營收/毛利/PE/PB/殖利率/股利/除權息)
├─ health.py       個股健診引擎(四面向連續計分+等級A–E+信心度)
├─ realtime_quote.py  即時五檔/報價(證交所 MIS) + 分時走勢(yfinance 1分K)
├─ news.py         個股新聞(Google News RSS)
├─ analyst.py      四師深度分析 + 綜合操作區間 + 手把手教學
├─ query.py        個股查詢(代號/名稱→完整分析，含健診/基本面/擴充指標)
├─ ../indicators.py  指標庫(MA/RSI/MACD/SuperTrend/BBand + OBV/DMI/威廉/CCI/寶塔/週月K resample)
├─ calibrate.py    自寫對齊單一ST的輕量回測(train/test偶奇、無look-ahead、研究用)
├─ track.py        訊號命中率回灌(事後評估真實勝率/R)
├─ daily_post.py   一鍵今日貼文(模板文案+主題海報，免LLM)
├─ eod.py          盤後一鍵管線(刷快取→籌碼→基本面預抓→掃描→貼文)
├─ loop.py/app.py/server.py   背景迴圈 / 桌面app / 看板伺服器(/api/stock,analyst,news,quote)
├─ dashboard.html  機構級金融終端看板(5分頁+個股深度頁+主題快照)
├─ tests/          ~100個單元測試(標準庫unittest、零依賴、不連網)
└─ *.bat           開啟看板 / 背景掃描 / 全市場掃描 / app_launch
```
執行期產物（state.json/history.json/signals_book.json/posts/、twdata/{cache,chips,margin,tdcc,fundamentals,news}/、calibrate_result.md）皆 gitignore。

## 要擴充
- **加股票**：往 `universe.py` 的 `INDUSTRIES` 塞 `(代號, 名稱)`
- **調訊號/溫度**：`scan.py` 的 `analyse_one()`/`build_state()`；用 `calibrate.py` 驗證別手拍
- **加籌碼維度**：仿 chips/margin/tdcc 風格接新 open data 端點
- **改前先跑測試**：`python -m unittest discover -s tests` 確保沒回歸

> 本看板僅為技術數據呈現，非投資建議，據此進出風險自負。量化阿森 Carson Quant
