# -*- coding: utf-8 -*-
"""
twse_price.py — 證交所/櫃買 官方日線價格(取代 yfinance；yfinance 抓台股常錯)

為什麼：yfinance 台股價格不可靠(實測 2330 顯示 2505、證交所官方 2410，差 4%)。
改用官方來源：
  上市(TWSE)  STOCK_DAY_ALL  一個 GET 抓全市場當日官方 OHLCV
              STOCK_DAY      單檔某月日線(回補歷史用)
  上櫃(TPEX)  daily close all + 單檔日線

寫進與既有相同格式的快取 twdata/cache/<code>_TW.csv(Date,Open,High,Low,Close,Volume)，
scan._read_cache 原封不動就能吃到『正確』的價。單位、原始價(不還原)與既有一致。
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request
import urllib.parse
from datetime import date, datetime
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE.parent.parent / "twdata" / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_COLS = ["Open", "High", "Low", "Close", "Volume"]


def _get(url: str, timeout: int = 25, retries: int = 2):
    """GET → text；含重試+遞增 backoff。"""
    last = None
    for a in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=_UA)
            return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read().decode("utf-8", "replace")
        except Exception as e:
            last = e
            time.sleep(0.6 * (a + 1))
    raise last


def _num(x):
    """'2,410.00'/'--'/'X0.00' → float 或 None。"""
    if x is None:
        return None
    s = str(x).replace(",", "").replace("X", "").strip()
    if s in ("", "--", "---", "null", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── 全市場當日(上市) ─────────────────────────────────────────────────────────
def fetch_twse_day_all() -> dict[str, dict]:
    """TWSE STOCK_DAY_ALL：全市場當日官方 OHLCV。回傳 {code: {date,open,high,low,close,volume}}。
    主用 openapi(乾淨JSON list、穩定)，失敗退 rwd(表格式)。此端點只給『最近交易日』無日期參數。"""
    out: dict[str, dict] = {}
    # 主：openapi(list of dict)
    try:
        rows = json.loads(_get("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"))
        for r in rows if isinstance(rows, list) else []:
            code = str(r.get("Code", "")).strip()
            c = _num(r.get("ClosingPrice"))
            if not code or c is None:
                continue
            out[code] = {"date": _roc_to_iso(r.get("Date")),
                         "Open": _num(r.get("OpeningPrice")) or c, "High": _num(r.get("HighestPrice")) or c,
                         "Low": _num(r.get("LowestPrice")) or c, "Close": c,
                         "Volume": _num(r.get("TradeVolume")) or 0}
        if out:
            return out
    except Exception:
        pass
    # 備：rwd(表格式，fields 陣列)
    try:
        d = json.loads(_get("https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?response=json"))
        if d.get("stat") == "OK":
            dt = _roc_to_iso(d.get("date"))
            for row in d.get("data") or []:
                if len(row) < 8:
                    continue
                c = _num(row[7])
                if c is None:
                    continue
                out[row[0].strip()] = {"date": dt, "Open": _num(row[4]) or c, "High": _num(row[5]) or c,
                                       "Low": _num(row[6]) or c, "Close": c, "Volume": _num(row[2]) or 0}
    except Exception:
        pass
    return out


# ── 全市場當日(上櫃) ─────────────────────────────────────────────────────────
def fetch_tpex_day_all() -> dict[str, dict]:
    """TPEX 上櫃當日全市場收盤。回傳 {code: {...}}。端點格式較常變動，失敗回空。"""
    url = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    out: dict[str, dict] = {}
    try:
        rows = json.loads(_get(url))
    except Exception:
        return out
    for r in rows if isinstance(rows, list) else []:
        code = str(r.get("SecuritiesCompanyCode") or r.get("Code") or "").strip()
        c = _num(r.get("Close") or r.get("ClosingPrice"))
        if not code or c is None:
            continue
        dt = _roc_to_iso(r.get("Date"))
        out[code] = {"date": dt,
                     "Open": _num(r.get("Open")) or c, "High": _num(r.get("High")) or c,
                     "Low": _num(r.get("Low")) or c, "Close": c,
                     "Volume": _num(r.get("TradingShares") or r.get("TradeVolume")) or 0}
    return out


# ── 上櫃(.TWO)官方日線：yfinance 抓上櫃全錯(6488差29%)，改用 twstock(官方) ──────
def fetch_twstock_daily(code: str, months_back: int = 9) -> pd.DataFrame | None:
    """twstock.Stock 直抓證交所/櫃買官方日線(上市上櫃皆正確)。回傳大寫欄位 DataFrame。
    上櫃(.TWO)價格 yfinance 不可靠(實測環球晶6488系統786 vs官方1105)，一律走這條。"""
    try:
        import twstock
    except Exception:
        return None
    from datetime import date as _d
    today = _d.today()
    y, m = today.year, today.month
    m -= (months_back - 1)
    while m <= 0:
        m += 12; y -= 1
    try:
        stk = twstock.Stock(code)
        data = stk.fetch_from(y, m)
    except Exception:
        return None
    if not data:
        return None
    recs = []
    for d in data:
        if d.close is None:
            continue
        recs.append((d.date, d.open or d.close, d.high or d.close,
                     d.low or d.close, d.close, d.capacity or 0))
    if not recs:
        return None
    df = pd.DataFrame(recs, columns=["Date"] + _COLS).set_index("Date")
    df.index = pd.to_datetime(df.index)
    return df[~df.index.duplicated(keep="last")].sort_index()


def clean_corrupt_caches(dry_run: bool = True) -> dict:
    """就地清 twdata/cache/*.csv 的『孤立垃圾列』(不依賴網路)：
      - 非日期 index(coerce→NaT) / Close 缺 / Close≤0
      - Close 相對『鄰近 21 日局部中位數』>5 倍或 <1/5(抓 822283、0.004 那種單列爆衝，
        但**不誤刪**長期上漲/下跌造成的合理價格區間，因局部中位數會跟著趨勢走)
    dry_run=True 只回報不寫檔(務必先 dry-run 確認每檔只刪 1-2 列再實做)。回統計。"""
    import glob
    scanned = flagged = 0
    details = []
    for f in glob.glob(str(CACHE_DIR / "*.csv")):
        scanned += 1
        try:
            df = pd.read_csv(f, index_col=0)
            idx = pd.to_datetime(df.index, errors="coerce")
            c = pd.to_numeric(df.get("Close"), errors="coerce")
            local = c.rolling(21, center=True, min_periods=5).median()
            local = local.fillna(c.median())
            bad = idx.isna().values | c.isna().values | (c <= 0).values
            with pd.option_context("mode.use_inf_as_na", True):
                spike = ((c > local * 5) | (c < local / 5)).fillna(False).values
            bad = bad | spike
            n_bad = int(bad.sum())
            if n_bad == 0:
                continue
            flagged += 1
            details.append((Path(f).stem, n_bad))
            if not dry_run:
                clean = df[~bad]
                clean.index = idx[~bad]
                if len(clean) < 20:
                    continue
                clean.index.name = "Date"
                clean[_COLS].to_csv(CACHE_DIR / Path(f).name)
                print(f"[twse] 清壞快取 {Path(f).stem}：刪 {n_bad} 孤立壞列，剩 {len(clean)} 筆", flush=True)
        except Exception:
            continue
    return {"scanned": scanned, "flagged": flagged, "details": details}


def rebuild_otc_cache(codes: list[str], months: int = 9, sleep: float = 0.5) -> int:
    """用 twstock 官方重建上櫃(.TWO)快取，覆蓋 yfinance 的錯誤資料。回傳成功檔數。"""
    ok = 0
    for i, code in enumerate(codes, 1):
        df = fetch_twstock_daily(code, months)
        if df is not None and len(df) >= 20:
            # 覆蓋(非合併)：yfinance 上櫃資料整段錯，不保留
            path = CACHE_DIR / f"{code}_TWO.csv"
            df[_COLS].to_csv(path)
            ok += 1
            tag = f"OK {len(df)}筆 收{df['Close'].iloc[-1]}"
        else:
            tag = "抓不到"
        print(f"[twse] 上櫃重建 {i}/{len(codes)} {code}：{tag}", flush=True)
        time.sleep(sleep)
    return ok


# ── 單檔某月日線(回補歷史) ───────────────────────────────────────────────────
def fetch_twse_month(code: str, yyyymmdd: str) -> pd.DataFrame | None:
    """TWSE STOCK_DAY：單檔、yyyymmdd 所在『整月』的日線。回傳大寫欄位 DataFrame(index=日期)。"""
    url = (f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY"
           f"?date={yyyymmdd}&stockNo={urllib.parse.quote(code)}&response=json")
    try:
        d = json.loads(_get(url))
    except Exception:
        return None
    if d.get("stat") != "OK" or not d.get("data"):
        return None
    # fields: 日期,成交股數,成交金額,開盤價,最高價,最低價,收盤價,漲跌價差,成交筆數
    recs = []
    for row in d["data"]:
        iso = _roc_to_iso(row[0])
        if not iso:
            continue
        c = _num(row[6])
        if c is None:
            continue
        recs.append((iso, _num(row[3]) or c, _num(row[4]) or c, _num(row[5]) or c, c, _num(row[1]) or 0))
    if not recs:
        return None
    df = pd.DataFrame(recs, columns=["Date"] + _COLS).set_index("Date")
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def _roc_to_iso(s) -> str | None:
    """民國日期 '1150701' 或 '115/07/01' → '2026-07-01'。"""
    if not s:
        return None
    s = str(s).strip().replace("/", "")
    if len(s) == 7 and s.isdigit():                    # 1150701
        y = int(s[:3]) + 1911
        return f"{y:04d}-{s[3:5]}-{s[5:7]}"
    return None


# ── 回補/刷新快取(取代 yfinance) ─────────────────────────────────────────────
def _cache_path(code: str) -> Path:
    p_tw = CACHE_DIR / f"{code}_TW.csv"
    p_two = CACHE_DIR / f"{code}_TWO.csv"
    if p_two.exists() and not p_tw.exists():
        return p_two
    return p_tw


def _merge_write(code: str, fresh: pd.DataFrame):
    """把 fresh 合併進既有快取(同日覆蓋、保留更早歷史)，寫回。"""
    path = _cache_path(code)
    fresh = fresh[_COLS]
    if path.exists():
        try:
            old = pd.read_csv(path, index_col=0, parse_dates=True)[_COLS]
            comb = pd.concat([old, fresh])
            comb = comb[~comb.index.duplicated(keep="last")].sort_index()
        except Exception:
            comb = fresh
    else:
        comb = fresh
    comb.to_csv(path)


def rebuild_history(codes: list[str], months: int = 9, sleep: float = 0.6) -> int:
    """用 TWSE STOCK_DAY 逐月回補近 months 個月官方日線，覆蓋(修正)既有 yfinance 快取。
    只做上市(.TW)；上櫃單檔另需 TPEX(此版先跳過，仍走既有)。回傳成功檔數。"""
    today = date.today()
    yms = []
    y, m = today.year, today.month
    for _ in range(months):
        yms.append(f"{y:04d}{m:02d}01")
        m -= 1
        if m == 0:
            m = 12; y -= 1
    ok = 0
    for i, code in enumerate(codes, 1):
        frames = []
        for ym in yms:
            df = fetch_twse_month(code, ym)
            if df is not None and len(df):
                frames.append(df)
            time.sleep(sleep)
        if frames:
            allm = pd.concat(frames)
            allm = allm[~allm.index.duplicated(keep="last")].sort_index()
            _merge_write(code, allm)
            ok += 1
        print(f"[twse] 回補 {i}/{len(codes)} {code}：{'OK ' + str(len(frames)) + '月' if frames else '無'}", flush=True)
    return ok


def refresh_latest() -> int:
    """用 STOCK_DAY_ALL(+TPEX) 把『當日官方 K』併進所有快取(每日盤後跑，1~2 個請求)。回傳更新檔數。"""
    day = fetch_twse_day_all()
    try:
        day.update({k: v for k, v in fetch_tpex_day_all().items() if k not in day})
    except Exception:
        pass
    n = 0
    for code, bar in day.items():
        iso = bar.get("date")
        if not iso:
            continue
        row = pd.DataFrame([[bar["Open"], bar["High"], bar["Low"], bar["Close"], bar["Volume"]]],
                           columns=_COLS, index=pd.to_datetime([iso]))
        # 只更新既有快取有的檔(宇宙內)，避免灌爆全市場
        if _cache_path(code).exists():
            _merge_write(code, row)
            n += 1
    return n


if __name__ == "__main__":
    import sys
    if "--latest" in sys.argv:
        print("[twse] 刷新當日官方 K …")
        print("[twse] 更新", refresh_latest(), "檔")
    elif "--clean" in sys.argv:
        apply = "--apply" in sys.argv       # 預設 dry-run，加 --apply 才真的寫檔
        print(f"[twse] {'實做' if apply else 'DRY-RUN'} 掃壞快取(孤立垃圾列)…")
        r = clean_corrupt_caches(dry_run=not apply)
        print(f"[twse] 掃 {r['scanned']} 檔，標記 {r['flagged']} 檔有孤立壞列")
        for name, n in r["details"][:40]:
            print(f"   {name}: {n} 列")
        if not apply:
            print("[twse] （dry-run，未寫檔；確認後加 --apply）")
    else:
        day = fetch_twse_day_all()
        print("STOCK_DAY_ALL 筆數", len(day))
        for c in ("2330", "0050", "2317", "2454", "2412"):
            print(" ", c, day.get(c))
