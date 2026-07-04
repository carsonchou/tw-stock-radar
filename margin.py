# -*- coding: utf-8 -*-
"""
margin.py — 數據獵手「融資融券 / 當沖籌碼」(R4，內容維度)

抓 TWSE 融資融券(MI_MARGN) + 當沖標的(TWTB4U)，落地本地快取(twdata/margin/)，供 scan.py 把
融資/融券/當沖面合進個股卡片做『顯示/confluence』(不宣稱 alpha、不硬 gate 訊號)。

資料來源
  上市 融資融券：https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN?date=YYYYMMDD&selectType=ALL&response=json
                (回傳 tables；個股明細表一列一檔：融資/融券 各 買進/賣出/現償/前日餘額/今日餘額/限額)
  上市 當沖標的：https://www.twse.com.tw/rwd/zh/afterTrading/TWTB4U?date=YYYYMMDD&response=json
                (每日當沖交易標的：當日沖銷成交股數 → 配 scan 的成交量算當沖比)
  上櫃：TPEX 對應端點 best-effort，抓不到就只上市、優雅降級。

解析雷(沿用 chips.py 風格)
  - 數字去逗號、'--'/''視 0；單位自動偵測(MI_MARGN 個股明細多為『張』，必要時 ÷1000)。
  - 融資/融券『今日餘額』『前日餘額』在明細表是重複欄名 → 用『出現順序』(第1組=融資、第2組=融券)定位。
  - 只收 4 位數普通股(排除 ETF 00 開頭/權證)。
  - 用實際有回傳資料的交易日當交易日(非推算)；盤後 T+0、盤中由 scan 標 T-1。

每檔輸出(load_margin)
  margin_balance(融資餘額張)、margin_chg(融資今日−前日)、short_balance(融券餘額張)、
  short_margin_ratio(券資比%=融券/融資×100)、day_trade_lots(當沖成交張；當沖比由 scan 配成交量算)。

用法
  python margin.py                  # 回補最近 5 個交易日(上市)到 twdata/margin/
  python margin.py --date 20260630  # 只抓指定日
  python margin.py --show 2603      # 顯示某代號融資券當沖
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
QS = HERE.parent
ROOT = QS.parent
MARGIN_DIR = ROOT / "twdata" / "margin"

sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 沿用 chips.py 的工具(同風格、不重造輪子；chips 不 import margin → 無循環)
from chips import (_to_lots, _is_stock_code, _roc, _get_json,      # noqa: E402
                   HEADERS, THROTTLE_SEC, MAX_LOOKBACK_CAL_DAYS, DEFAULT_DAYS)


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _num(raw) -> int:
    """去逗號轉 int(股或張原值，不除)。'--'/''→0。"""
    if raw is None:
        return 0
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "--", "X", "x"):
        return 0
    try:
        return int(round(float(s)))
    except (TypeError, ValueError):
        return 0


# ── TWSE 融資融券 MI_MARGN ──────────────────────────────────────────────────
def _find_margin_table(tables: list[dict]) -> dict | None:
    """從 MI_MARGN 的多表中挑『個股明細表』：列數最多、首欄為 4 碼代號、欄數 15~17。"""
    best = None
    for t in tables:
        rows = t.get("data") or []
        nf = len(t.get("fields") or [])
        if not rows or nf < 14:
            continue
        sample = sum(1 for r in rows[:20] if r and len(str(r[0]).strip()) == 4
                     and str(r[0]).strip().isdigit())
        if sample >= 5 and (best is None or len(rows) > len(best.get("data") or [])):
            best = t
    return best


def _margin_col_index(fields: list[str]) -> dict | None:
    """用欄名出現順序定位融資/融券 今日餘額、前日餘額。
    明細表融資在前、融券在後；『今日餘額』『前日餘額』各出現兩次。"""
    today_idx = [i for i, f in enumerate(fields) if "今日餘額" in str(f)]
    prev_idx = [i for i, f in enumerate(fields) if "前日餘額" in str(f)]
    if len(today_idx) >= 2 and len(prev_idx) >= 1:
        return {"m_today": today_idx[0], "m_prev": prev_idx[0], "s_today": today_idx[1]}
    return None


# MI_MARGN 個股明細的『典型』欄位索引(欄名定位失敗時的後備)：
#  0代號 1名稱 | 融資:2買 3賣 4現償 5前餘 6今餘 7限額 | 融券:8買 9賣 10現償 11前餘 12今餘 13限額
_MI_FALLBACK = {"m_today": 6, "m_prev": 5, "s_today": 12}


def fetch_margin_twse(d: date) -> dict[str, dict] | None:
    """抓上市某日融資融券。回傳 {code:{margin_balance, margin_prev, short_balance}}(原始單位)。
    非交易日/無資料 → None。單位後續由 _finalize 自動偵測 ÷1000。"""
    ymd = d.strftime("%Y%m%d")
    url = (f"https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
           f"?date={ymd}&selectType=ALL&response=json")
    j = _get_json(url)
    if not isinstance(j, dict) or j.get("stat") != "OK":
        return None
    tables = j.get("tables")
    table = _find_margin_table(tables) if tables else (
        {"fields": j.get("fields", []), "data": j.get("data", [])} if j.get("data") else None)
    if not table:
        return None
    fields = table.get("fields") or []
    rows = table.get("data") or []
    ci = _margin_col_index(fields) or _MI_FALLBACK
    need = max(ci.values())
    out: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) <= need:
            continue
        code = str(row[0]).strip()
        if not _is_stock_code(code):
            continue
        out[code] = {"margin_balance": _num(row[ci["m_today"]]),
                     "margin_prev": _num(row[ci["m_prev"]]),
                     "short_balance": _num(row[ci["s_today"]])}
    return out or None


# ── TWSE 當沖 TWTB4U ────────────────────────────────────────────────────────
def fetch_daytrade_twse(d: date) -> dict[str, int] | None:
    """抓上市某日當沖成交股數。回傳 {code: day_trade_shares(股)}。非交易日 → None。"""
    ymd = d.strftime("%Y%m%d")
    # 當沖『成交量值』報表：走 exchangeReport(非 rwd)；資料在 tables[1]『當日沖銷交易標的
    # 及成交量值』(欄 index 3=當日沖銷交易成交股數)。rwd 路徑 404、openapi TWTB4U 只有暫停註記。
    url = (f"https://www.twse.com.tw/exchangeReport/TWTB4U"
           f"?response=json&date={ymd}&selectType=All")
    j = _get_json(url)
    if not isinstance(j, dict):
        return None
    tables = j.get("tables")
    if tables:
        table = max(tables, key=lambda t: len(t.get("data") or []))
        fields, rows = table.get("fields") or [], table.get("data") or []
    else:
        fields, rows = j.get("fields") or [], j.get("data") or []
    if not rows:
        return None
    # 當沖成交股數欄：欄名含「當日沖銷」且「成交股數」(非金額)；找不到用索引3後備
    sh_idx = next((i for i, f in enumerate(fields)
                   if "成交股數" in str(f) and "金額" not in str(f)), None)
    if sh_idx is None:
        sh_idx = next((i for i, f in enumerate(fields) if "股數" in str(f)), 3)
    out: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) <= sh_idx:
            continue
        code = str(row[0]).strip()
        if not _is_stock_code(code):
            continue
        out[code] = _num(row[sh_idx])
    return out or None


# ── 上櫃 best-effort(抓不到就略過、只上市) ──────────────────────────────────
def fetch_margin_tpex(d: date) -> dict[str, dict] | None:
    """上櫃融資融券(best-effort)。TPEX 端點格式易變，抓不到回 None、優雅降級。"""
    url = ("https://www.tpex.org.tw/web/stock/margin_trading/margin_balance/margin_bal_result.php"
           f"?l=zh-tw&d={_roc(d)}&o=json")
    j = _get_json(url)
    if not isinstance(j, dict):
        return None
    tables = j.get("tables")
    rows = (tables[0].get("data") if tables else j.get("aaData")) or j.get("data") or []
    if not rows:
        return None
    out: dict[str, dict] = {}
    for row in rows:
        try:
            code = str(row[0]).strip()
        except (IndexError, TypeError):
            continue
        if not _is_stock_code(code) or len(row) < 13:
            continue
        # TPEX 融資明細欄序與上市相近(融資前餘/買/賣/現償/今餘…)；用後備索引、抓不到的留空
        try:
            out[code] = {"margin_balance": _num(row[6]), "margin_prev": _num(row[2]),
                         "short_balance": _num(row[12])}
        except IndexError:
            continue
    return out or None


# ── 單日合併 + 快取 ─────────────────────────────────────────────────────────
def _day_file(d: date) -> Path:
    return MARGIN_DIR / f"{d.isoformat()}.json"


def _detect_div(margin_map: dict[str, dict]) -> int:
    """單位自動偵測：個股融資餘額中位數 > 30萬 → 視為『股』需 ÷1000；否則已是『張』。"""
    vals = [v["margin_balance"] for v in margin_map.values() if v["margin_balance"] > 0]
    if not vals:
        return 1
    vals.sort()
    med = vals[len(vals) // 2]
    return 1000 if med > 300_000 else 1


def fetch_day(d: date) -> dict[str, dict] | None:
    """抓某日 上市(+上櫃 best-effort) 融資融券 + 當沖，合併成每檔 dict(單位=張)。"""
    margin = fetch_margin_twse(d)
    time.sleep(THROTTLE_SEC)
    if margin:
        tpex = fetch_margin_tpex(d)
        if tpex:
            margin.update(tpex)
            time.sleep(THROTTLE_SEC)
    if not margin:
        return None                       # 融資券抓不到 → 視為非交易日/失敗
    dt = fetch_daytrade_twse(d)
    dt_ok = dt is not None              # 當沖『整段』是否抓到(區分無資料 vs 真 0)
    dt = dt or {}
    time.sleep(THROTTLE_SEC)

    div = _detect_div(margin)
    out: dict[str, dict] = {}
    for code, m in margin.items():
        mb = m["margin_balance"] // div
        mp = m["margin_prev"] // div
        sb = m["short_balance"] // div
        smr = round(sb / mb * 100, 1) if mb > 0 else 0.0
        # 當沖整段抓失敗 → None(無資料，別誤顯示 0%)；抓到了但該股無當沖列 → 0(真 0)
        dt_lots = None if not dt_ok else (dt.get(code, 0) // 1000)
        out[code] = {"margin_balance": mb, "margin_chg": mb - mp,
                     "short_balance": sb, "short_margin_ratio": smr,
                     "day_trade_lots": dt_lots}
    return out or None


def load_day_cache(d: date) -> dict[str, dict] | None:
    p = _day_file(d)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("data") or None
    except Exception:
        return None


def ensure_day(d: date) -> dict[str, dict] | None:
    cached = load_day_cache(d)
    if cached is not None:
        return cached
    data = fetch_day(d)
    if data is None:
        return None
    _atomic_write_json(_day_file(d), {
        "date": d.isoformat(), "n": len(data),
        "fetched": datetime.now().isoformat(timespec="seconds"), "data": data})
    return data


# ── 交易日序列(用實際有資料的日期) ─────────────────────────────────────────
def recent_trading_days(days: int = DEFAULT_DAYS, end: date | None = None,
                        offline: bool = False) -> list[date]:
    end = end or (date.today() - timedelta(days=1))
    got: list[date] = []
    cur = end
    scanned = 0
    while len(got) < days and scanned < MAX_LOOKBACK_CAL_DAYS:
        scanned += 1
        if cur.weekday() < 5:
            if offline:
                if load_day_cache(cur):
                    got.append(cur)
            else:
                was_cached = _day_file(cur).exists()
                if ensure_day(cur):
                    got.append(cur)
                    if not was_cached:
                        time.sleep(THROTTLE_SEC)
        cur -= timedelta(days=1)
    return got


def backfill(days: int = DEFAULT_DAYS, end: date | None = None) -> list[date]:
    td = recent_trading_days(days, end)
    print(f"[margin] 已備妥 {len(td)} 個交易日："
          f"{', '.join(d.isoformat() for d in td) if td else '(無)'}")
    return td


# ── 對外：載入最近交易日每檔融資券當沖 ─────────────────────────────────────
def load_margin(codes=None, days: int = 2, end: date | None = None,
                offline: bool = False) -> dict[str, dict]:
    """回傳 {code:{margin_balance, margin_chg, short_balance, short_margin_ratio,
                   day_trade_lots, t_minus, date}}(單位張，day_trade 為張)。
    只取最近一個交易日值(融資券是當日餘額，不需多日)。codes 給定時取交集。"""
    td = recent_trading_days(days, end, offline=offline)
    if not td:
        return {}
    latest = td[0]
    data = load_day_cache(latest) if offline else (load_day_cache(latest) or ensure_day(latest))
    if not data:
        return {}
    code_set = set(codes) if codes else None
    out: dict[str, dict] = {}
    for code, m in data.items():
        if code_set is not None and code not in code_set:
            continue
        out[code] = {**m, "t_minus": 0, "date": latest.isoformat()}
    return out


def latest_margin_date(days: int = 2, end: date | None = None,
                       offline: bool = False) -> str | None:
    td = recent_trading_days(days, end, offline=offline)
    return td[0].isoformat() if td else None


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--date", type=str, default=None, help="只抓指定日(YYYYMMDD)")
    ap.add_argument("--show", type=str, default=None, help="顯示某代號")
    args = ap.parse_args()

    if args.date:
        d = datetime.strptime(args.date, "%Y%m%d").date()
        data = ensure_day(d)
        if data is None:
            print(f"[margin] {d.isoformat()} 非交易日或無資料")
        else:
            print(f"[margin] {d.isoformat()} 取得 {len(data)} 檔(已快取)")
            for c in ("2330", "2603", "2609"):
                if c in data:
                    m = data[c]
                    print(f"    {c}: 融資{m['margin_balance']}張(增減{m['margin_chg']:+}) "
                          f"融券{m['short_balance']}張 券資比{m['short_margin_ratio']}% "
                          f"當沖{m['day_trade_lots']}張")
        return

    backfill(args.days)
    if args.show:
        m = load_margin([args.show], days=args.days).get(args.show)
        if m:
            print(f"[margin] {args.show}: 融資{m['margin_balance']}張(增減{m['margin_chg']:+}) "
                  f"融券{m['short_balance']}張 券資比{m['short_margin_ratio']}% "
                  f"當沖{m['day_trade_lots']}張 (as of {m['date']})")
        else:
            print(f"[margin] {args.show} 無資料")


if __name__ == "__main__":
    main()
