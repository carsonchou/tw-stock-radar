# -*- coding: utf-8 -*-
"""
fundamentals.py — 數據獵手「基本面/估值」資料層（對齊三竹的財務面）

三竹強在個股健診有「財務面」，我們原本完全沒有。這支補齊：
  估值(每日全市場一次) 證交所 BWIBBU_ALL(免 token)：本益比 PE / 殖利率 / 股價淨值比 PB
  財報(季)           FinMind TaiwanStockFinancialStatements：EPS、毛利率、營益率
  月營收(月)          FinMind TaiwanStockMonthRevenue：營收、年增 YoY、月增 MoM
  股利(年)           FinMind TaiwanStockDividend：現金/股票股利

快取比照 chips.py/margin.py/tdcc.py 模式，落地 twdata/fundamentals/：
  valuation_<YYYYMMDD>.json   全市場當日估值(一次抓)
  stock_<code>.json           單檔財報/營收/股利(含 fetched_at，長 TTL)

設計原則：離線(offline=True)只讀快取、秒回不卡；缺資料一律優雅降級成 None，
不阻塞健診/查詢。FinMind 免 token 300 req/hr、有 FINMIND_TOKEN env 則 600/hr。

用法
  python fundamentals.py 2330            # 顯示單檔基本面(缺快取會即時抓一次)
  python fundamentals.py --valuation     # 刷新全市場估值(PE/PB/殖利率)
  python fundamentals.py --refresh 2330 2317 2454   # 批次刷新指定檔財報/營收/股利
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
FUND_DIR = HERE / "twdata" / "fundamentals"
FUND_DIR.mkdir(parents=True, exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.environ.get("FINMIND_TOKEN", "").strip()
BWIBBU_URL = "https://openapi.twse.com.tw/v1/exchangeReport/BWIBBU_ALL"

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DataHunter/1.0",
       "Accept": "application/json, text/plain, */*"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE

# 快取新鮮度：估值日更、財報季更(給寬鬆 7 天)、營收月更
STOCK_TTL_DAYS = 7


# ── 通用工具 ────────────────────────────────────────────────────────────────
def _get(url: str, timeout: int = 25, retries: int = 2):
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
    """'12.34'/'--'/'' → float 或 None。"""
    if x is None:
        return None
    s = str(x).replace(",", "").strip()
    if s in ("", "--", "---", "null", "None", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ── 估值：全市場當日 PE/PB/殖利率(證交所 BWIBBU_ALL，免 token) ─────────────────
def fetch_valuation_all() -> dict:
    """證交所 BWIBBU_ALL：全市場當日本益比/殖利率/股價淨值比。
    回 {code: {pe, pb, dividend_yield}}；同時落快取 valuation_<today>.json。"""
    out: dict[str, dict] = {}
    try:
        rows = json.loads(_get(BWIBBU_URL))
    except Exception:
        return out
    for r in rows if isinstance(rows, list) else []:
        code = str(r.get("Code") or r.get("證券代號") or "").strip()
        if not code:
            continue
        out[code] = {
            "pe": _num(r.get("PEratio") or r.get("本益比")),
            "dividend_yield": _num(r.get("DividendYield") or r.get("殖利率(%)")),
            "pb": _num(r.get("PBratio") or r.get("股價淨值比")),
        }
    if out:
        stamp = date.today().strftime("%Y%m%d")
        _atomic_write_json(FUND_DIR / f"valuation_{stamp}.json",
                           {"date": stamp, "data": out})
    return out


def load_valuation(offline: bool = True) -> dict:
    """讀最近一份估值快取 → {code: {pe,pb,dividend_yield}}。offline 且無快取→抓一次(或空)。"""
    files = sorted(FUND_DIR.glob("valuation_*.json"), reverse=True)
    if files:
        try:
            return json.loads(files[0].read_text(encoding="utf-8")).get("data", {})
        except Exception:
            pass
    if not offline:
        return fetch_valuation_all()
    return {}


# ── FinMind：財報/營收/股利(單檔) ────────────────────────────────────────────
def _finmind(dataset: str, data_id: str, start_date: str) -> list:
    """FinMind v4 data API 通用取數；失敗回空 list(優雅降級)。"""
    params = {"dataset": dataset, "data_id": data_id, "start_date": start_date}
    if FINMIND_TOKEN:
        params["token"] = FINMIND_TOKEN
    url = FINMIND_URL + "?" + urllib.parse.urlencode(params)
    try:
        j = json.loads(_get(url, timeout=25))
    except Exception:
        return []
    if isinstance(j, dict) and j.get("status") == 200:
        return j.get("data") or []
    return []


def _latest_quarters(rows: list, type_key: str, n: int = 5) -> list:
    """從 FinancialStatements rows 取某 type 的最近 n 季 (date, value)，新到舊。"""
    seq = [(r.get("date"), _num(r.get("value"))) for r in rows
           if r.get("type") == type_key and _num(r.get("value")) is not None]
    seq.sort(key=lambda x: x[0] or "", reverse=True)
    return seq[:n]


def fetch_stock_fundamentals(code: str) -> dict:
    """抓單檔 EPS/毛利率/營益率(季) + 月營收 YoY + 股利，落快取 stock_<code>.json。
    回精簡 dict；任何子項抓不到就 None。"""
    two_years_ago = f"{date.today().year - 2}-01-01"
    out = {"code": code, "fetched_at": datetime.now().isoformat(timespec="seconds"),
           "eps_q": None, "eps_ttm": None, "eps_yoy": None,
           "gross_margin": None, "op_margin": None,
           "rev": None, "rev_yoy": None, "rev_mom": None,
           "cash_div": None, "stock_div": None, "div_year": None, "ex_date": None}

    # 財報：EPS + 毛利率 + 營益率
    fs = _finmind("TaiwanStockFinancialStatements", code, two_years_ago)
    if fs:
        eps = _latest_quarters(fs, "EPS", 5)
        if eps:
            out["eps_q"] = eps[0][1]
            if len(eps) >= 4:
                out["eps_ttm"] = round(sum(v for _, v in eps[:4]), 2)
            # 年增：最近一季 vs 去年同季(相隔 4 季)
            if len(eps) >= 5 and eps[4][1] not in (None, 0):
                out["eps_yoy"] = round((eps[0][1] - eps[4][1]) / abs(eps[4][1]) * 100, 1)
        rev_q = _latest_quarters(fs, "Revenue", 1)
        gp_q = _latest_quarters(fs, "GrossProfit", 1)
        oi_q = _latest_quarters(fs, "OperatingIncome", 1)
        if rev_q and rev_q[0][1]:
            if gp_q:
                out["gross_margin"] = round(gp_q[0][1] / rev_q[0][1] * 100, 1)
            if oi_q:
                out["op_margin"] = round(oi_q[0][1] / rev_q[0][1] * 100, 1)

    # 月營收：最新月 + YoY + MoM
    mr = _finmind("TaiwanStockMonthRevenue", code, two_years_ago)
    if mr:
        seq = [(r.get("date"), _num(r.get("revenue"))) for r in mr if _num(r.get("revenue")) is not None]
        seq.sort(key=lambda x: x[0] or "", reverse=True)
        if seq:
            out["rev"] = seq[0][1]
            if len(seq) >= 13 and seq[12][1]:
                out["rev_yoy"] = round((seq[0][1] - seq[12][1]) / abs(seq[12][1]) * 100, 1)
            if len(seq) >= 2 and seq[1][1]:
                out["rev_mom"] = round((seq[0][1] - seq[1][1]) / abs(seq[1][1]) * 100, 1)

    # 股利政策(最近一年)
    dv = _finmind("TaiwanStockDividend", code, two_years_ago)
    if dv:
        dv2 = sorted(dv, key=lambda r: r.get("date") or "", reverse=True)
        cash = _num(dv2[0].get("CashEarningsDistribution")) if dv2 else None
        stock = _num(dv2[0].get("StockEarningsDistribution")) if dv2 else None
        out["cash_div"] = cash
        out["stock_div"] = stock
        out["div_year"] = (dv2[0].get("date") or "")[:4] if dv2 else None
        # 除權息交易日(現金優先，缺則股票除權日)；'0'/空視為無
        if dv2:
            ex = dv2[0].get("CashExDividendTradingDate") or dv2[0].get("StockExDividendTradingDate")
            out["ex_date"] = ex if (ex and str(ex) not in ("0", "", "None")) else None

    _atomic_write_json(FUND_DIR / f"stock_{code}.json", out)
    return out


def _cache_fresh(path: Path, max_age_days: int) -> bool:
    if not path.exists():
        return False
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        t = datetime.fromisoformat(obj.get("fetched_at"))
        return (datetime.now() - t).days < max_age_days
    except Exception:
        return False


def load_stock_fundamentals(code: str, offline: bool = True,
                            max_age_days: int = STOCK_TTL_DAYS) -> dict | None:
    """讀單檔財報/營收/股利快取；offline 只讀(缺/過期→None)，非 offline 過期會抓一次。"""
    path = FUND_DIR / f"stock_{code}.json"
    if _cache_fresh(path, max_age_days):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    if not offline:
        return fetch_stock_fundamentals(code)
    # offline：即使過期也回舊資料(財報變動慢)，完全沒有才 None
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ── 對外：合併單檔完整基本面(估值 + 財報/營收/股利) ──────────────────────────
def load_fundamentals(code: str, offline: bool = True) -> dict:
    """健診/查詢用：合併估值(全市場快取)＋單檔財報快取 → 一個扁平 dict，缺項 None。"""
    out = {"pe": None, "pb": None, "dividend_yield": None,
           "eps_q": None, "eps_ttm": None, "eps_yoy": None,
           "gross_margin": None, "op_margin": None,
           "rev": None, "rev_yoy": None, "rev_mom": None,
           "cash_div": None, "stock_div": None, "div_year": None, "ex_date": None,
           "has_valuation": False, "has_financials": False}
    val = load_valuation(offline=offline).get(code)
    if val:
        out.update({"pe": val.get("pe"), "pb": val.get("pb"),
                    "dividend_yield": val.get("dividend_yield")})
        out["has_valuation"] = any(v is not None for v in val.values())
    fs = load_stock_fundamentals(code, offline=offline)
    if fs:
        for k in ("eps_q", "eps_ttm", "eps_yoy", "gross_margin", "op_margin",
                  "rev", "rev_yoy", "rev_mom", "cash_div", "stock_div", "div_year"):
            out[k] = fs.get(k)
        out["has_financials"] = any(fs.get(k) is not None
                                    for k in ("eps_q", "eps_ttm", "rev_yoy", "gross_margin"))
    return out


# ── 批次刷新(給每日/每週排程或 CLI) ──────────────────────────────────────────
def prefetch(codes: list[str], max_financials: int = 30, sleep: float = 1.0) -> dict:
    """盤後背景預抓：估值(BWIBBU 全市場 1 次，免費) + 輪替刷新最舊/缺的財報(FinMind)。
    FinMind 免 token 300 req/hr、每檔 3 call → 上限 max_financials(預設 30=90 call)遠低於限額，
    多天輪完整個精選宇宙(財報季更、慢刷可接受)。回統計。讓常見股基本面秒回、不卡首查。"""
    val = fetch_valuation_all()
    # 挑「最該刷」的：無快取 或 最舊；已在 TTL 內的跳過
    def _age(c):
        p = FUND_DIR / f"stock_{c}.json"
        if not p.exists():
            return 1e9                       # 無快取最優先
        try:
            t = datetime.fromisoformat(json.loads(p.read_text(encoding="utf-8")).get("fetched_at"))
            return (datetime.now() - t).total_seconds()
        except Exception:
            return 1e9
    stale = sorted((c for c in codes if _age(c) > STOCK_TTL_DAYS * 86400),
                   key=_age, reverse=True)[:max_financials]
    ok = 0
    for c in stale:
        d = fetch_stock_fundamentals(c)
        ok += 1 if (d.get("eps_ttm") is not None or d.get("rev_yoy") is not None) else 0
        time.sleep(sleep)
    return {"valuation": len(val), "financials_refreshed": ok, "financials_stale": len(stale)}


def refresh_stocks(codes: list[str], sleep: float = 1.2) -> int:
    ok = 0
    for i, c in enumerate(codes, 1):
        d = fetch_stock_fundamentals(c)
        got = d.get("eps_ttm") is not None or d.get("rev_yoy") is not None
        ok += 1 if got else 0
        print(f"[fund] {i}/{len(codes)} {c}: "
              f"EPS_ttm={d.get('eps_ttm')} 營收YoY={d.get('rev_yoy')} 毛利={d.get('gross_margin')}", flush=True)
        time.sleep(sleep)
    return ok


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--valuation" in args:
        v = fetch_valuation_all()
        print(f"[fund] 估值刷新 {len(v)} 檔")
        for c in ("2330", "2317", "2412", "0050"):
            print(" ", c, v.get(c))
    elif "--refresh" in args:
        codes = [a for a in args if a.isdigit()]
        print(f"[fund] 批次刷新 {len(codes)} 檔財報/營收/股利 …")
        print("[fund] 成功", refresh_stocks(codes), "檔")
    elif args and args[0].isdigit():
        code = args[0]
        # 缺快取就即時抓一次(非 offline)
        if not (FUND_DIR / f"stock_{code}.json").exists():
            fetch_stock_fundamentals(code)
        if not list(FUND_DIR.glob("valuation_*.json")):
            fetch_valuation_all()
        f = load_fundamentals(code, offline=True)
        print(f"=== {code} 基本面 ===")
        for k, v in f.items():
            print(f"  {k:16} {v}")
    else:
        print(__doc__)
