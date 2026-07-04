# -*- coding: utf-8 -*-
"""
chips.py — 數據獵手「三大法人籌碼面」(Track A)

抓 TWSE(上市 T86) + TPEX(上櫃) 每日三大法人買賣超，落地本地快取(twdata/chips/)，
供 scan.py 在「收盤確認」路徑把籌碼面合進個股訊號(做多加分，非硬 gate)。

資料來源
  TWSE 上市：https://www.twse.com.tw/rwd/zh/fund/T86?date=YYYYMMDD&selectType=ALL&response=json
            (回傳 fields + data，一列一檔；selectType=ALL 為「含外資自營/避險」完整 19 欄格式)
  TPEX 上櫃：https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php
            ?l=zh-tw&se=AL&t=D&d=民國/MM/DD&o=json  (24 欄含外資自營/避險；可帶日期回補歷史)
            (openapi 版 tpex_3insti_daily_trading 只給最新一日，回補不夠用，故用此帶日期端點)

解析雷(務必)
  - 單位「股」÷1000 成「張」(四捨五入取整)。
  - 數字含逗號字串先去逗號;'--'/'' 視為 0。
  - 外資淨買 = 「外資及陸資(不含自營)買賣超」+「外資自營商買賣超」兩欄相加(= 含外資自營)。
  - 投信淨買 = 單欄。
  - 三大法人 = 官方合計欄(= 外資含自營 + 投信 + 自營商)。
  - 自營商雜訊大 → 法人「確認」(chip_confirm)只用 外資+投信、不含自營。

交易日
  逐日往回抓，用「實際有回傳資料的日期」當交易日(非推算)，避開連假把連買天數算錯。
  非交易日(週末/例假)端點回空 → 跳過、不快取、不計入。

用法
  python chips.py                  # 回補最近 5 個交易日(上市+上櫃)到 twdata/chips/
  python chips.py --days 8         # 回補 8 個交易日
  python chips.py --date 20260630  # 只抓指定日(YYYYMMDD)
  python chips.py --show 2330      # 顯示某代號近 N 日籌碼
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
QS = HERE.parent                       # quant-service/
ROOT = QS.parent                       # carson-agent/
CHIPS_DIR = ROOT / "twdata" / "chips"  # 每交易日一檔 JSON(本地快取)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# 節流：對證交所/櫃買請求間隔(秒)，避免被擋
THROTTLE_SEC = 2.5
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36 DataHunter/1.0"),
    "Accept": "application/json, text/plain, */*",
}
DEFAULT_DAYS = 5
MAX_LOOKBACK_CAL_DAYS = 25             # 回補時最多往回找幾個日曆日(收齊 N 交易日的上限)


# ── 通用工具 ────────────────────────────────────────────────────────────────
def _atomic_write_json(path: Path, obj) -> None:
    """原子寫入(寫暫存→os.replace)；與 scan._atomic_write_json 同手法，避免讀到寫一半。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _is_stock_code(code: str) -> bool:
    """4 位數普通股代號；排除 ETF/ETN(00 開頭)、權證/受益證券(非純 4 碼數字)。"""
    return len(code) == 4 and code.isdigit() and not code.startswith("00")


def _to_lots(raw) -> int:
    """把含逗號的『股』字串轉成『張』(÷1000 四捨五入)。'--'/''/None → 0。"""
    if raw is None:
        return 0
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "--", "X", "x"):
        return 0
    try:
        return int(round(float(s) / 1000.0))
    except (TypeError, ValueError):
        return 0


def _roc(d: date) -> str:
    """西元 date → 民國『115/06/30』。"""
    return f"{d.year - 1911}/{d.month:02d}/{d.day:02d}"


def _get_json(url: str, timeout: int = 25) -> dict | list | None:
    import requests
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


# ── TWSE 上市 T86 ───────────────────────────────────────────────────────────
def _t86_field_index(fields: list[str]) -> dict[str, int] | None:
    """以欄位『名稱關鍵字』定位(對格式漂移較穩；不寫死索引)。
    需要：外資不含自營買賣超 / 外資自營買賣超 / 投信買賣超 / 三大法人買賣超。"""
    idx = {"foreign_excl": None, "foreign_dealer": None, "trust": None, "total": None}
    for i, f in enumerate(fields):
        name = str(f)
        if "三大法人" in name and "買賣超" in name:
            idx["total"] = i
        elif "投信" in name and "買賣超" in name:
            idx["trust"] = i
        elif "外資自營商" in name and "買賣超" in name and "不含" not in name:
            idx["foreign_dealer"] = i
        elif "外" in name and "買賣超" in name and "不含外資自營" in name:
            idx["foreign_excl"] = i
    # foreign_excl / trust / total 必要；foreign_dealer 缺(舊格式無此欄)時當 0
    if idx["foreign_excl"] is None or idx["trust"] is None or idx["total"] is None:
        return None
    return idx


def fetch_t86(d: date) -> dict[str, dict] | None:
    """抓 TWSE 上市某日三大法人。回傳 {code: {foreign_net, trust_net, instinv_net}}(張)。
    非交易日/無資料 → None。"""
    ymd = d.strftime("%Y%m%d")
    url = (f"https://www.twse.com.tw/rwd/zh/fund/T86"
           f"?date={ymd}&selectType=ALL&response=json")
    j = _get_json(url)
    if not isinstance(j, dict) or j.get("stat") != "OK":
        return None
    fields = j.get("fields") or []
    data = j.get("data") or []
    if not data:
        return None
    fi = _t86_field_index(fields)
    if fi is None:
        return None
    need = max(v for v in fi.values() if v is not None)
    out: dict[str, dict] = {}
    for row in data:
        if not isinstance(row, list) or len(row) <= need:
            continue                       # 欄位不足的雜列(小計/說明) → 跳過
        code = str(row[0]).strip()
        if not _is_stock_code(code):       # 濾掉 ETF(00開頭)/權證/受益證券，只留普通股
            continue
        f_excl = _to_lots(row[fi["foreign_excl"]])
        f_deal = _to_lots(row[fi["foreign_dealer"]]) if fi["foreign_dealer"] is not None else 0
        foreign = f_excl + f_deal
        trust = _to_lots(row[fi["trust"]])
        inst = _to_lots(row[fi["total"]])
        out[code] = {"foreign_net": foreign, "trust_net": trust, "instinv_net": inst}
    return out or None


# ── TPEX 上櫃(帶日期端點，24 欄含避險) ──────────────────────────────────────
# 固定欄位索引(se=AL 避險版 24 欄；以 sum 檢核過)：
#   0 代號 1 名稱
#   4 外資及陸資(不含自營)買賣超   7 外資自營商買賣超
#  13 投信買賣超                 23 三大法人買賣超合計
_TPEX_IDX = {"foreign_excl": 4, "foreign_dealer": 7, "trust": 13, "total": 23}


def fetch_tpex(d: date) -> dict[str, dict] | None:
    """抓 TPEX 上櫃某日三大法人。回傳 {code: {foreign_net, trust_net, instinv_net}}(張)。
    非交易日/無資料 → None。"""
    url = ("https://www.tpex.org.tw/web/stock/3insti/daily_trade/3itrade_hedge_result.php"
           f"?l=zh-tw&se=AL&t=D&d={_roc(d)}&o=json")
    j = _get_json(url)
    if not isinstance(j, dict):
        return None
    tables = j.get("tables") or []
    table = tables[0] if tables else j      # 新舊回傳結構相容
    rows = table.get("data") or []
    if not rows:
        return None
    ncol = len(table.get("fields") or [])
    if ncol and ncol < 24:                  # 欄數不符避險版 → 不硬解(避免錯位)
        return None
    out: dict[str, dict] = {}
    for row in rows:
        try:
            code = str(row[0]).strip()
        except (IndexError, TypeError):
            continue
        if not _is_stock_code(code):       # 濾掉 ETF/權證/受益證券，只留普通股
            continue
        try:
            foreign = _to_lots(row[_TPEX_IDX["foreign_excl"]]) + _to_lots(row[_TPEX_IDX["foreign_dealer"]])
            trust = _to_lots(row[_TPEX_IDX["trust"]])
            inst = _to_lots(row[_TPEX_IDX["total"]])
        except IndexError:
            continue
        out[code] = {"foreign_net": foreign, "trust_net": trust, "instinv_net": inst}
    return out or None


# ── 單日合併 + 快取 ─────────────────────────────────────────────────────────
def _day_file(d: date) -> Path:
    return CHIPS_DIR / f"{d.isoformat()}.json"


def fetch_day(d: date) -> dict[str, dict] | None:
    """抓某日 上市+上櫃 合併。任一市場有資料即視為交易日；兩者皆空 → None(非交易日)。"""
    twse = fetch_t86(d)
    time.sleep(THROTTLE_SEC)               # 兩請求間節流
    tpex = fetch_tpex(d)
    if not twse and not tpex:
        return None
    merged: dict[str, dict] = {}
    merged.update(twse or {})
    merged.update(tpex or {})              # 代號不重疊(上市/上櫃互斥)
    return merged or None


def load_day_cache(d: date) -> dict[str, dict] | None:
    p = _day_file(d)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj.get("data") or None
    except Exception:
        return None


def ensure_day(d: date, force: bool = False) -> dict[str, dict] | None:
    """取某日籌碼：優先快取，無則連網抓並落檔。回傳 data 或 None(非交易日)。"""
    if not force:
        cached = load_day_cache(d)
        if cached is not None:
            return cached
    data = fetch_day(d)
    if data is None:
        return None
    _atomic_write_json(_day_file(d), {
        "date": d.isoformat(),
        "n_twse": sum(1 for v in data.values()),   # 合併後總檔數(概略)
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "data": data,
    })
    return data


# ── 交易日序列(用實際有資料的日期) ─────────────────────────────────────────
def recent_trading_days(days: int = DEFAULT_DAYS, end: date | None = None,
                        offline: bool = False) -> list[date]:
    """從 end(預設昨天)往回，收集 `days` 個『實際有籌碼資料』的交易日(新→舊)。
    冷啟動會連網回補；已快取的日子直接命中。逐日節流。
    offline=True：只認本地已快取的交易日(不連網，給 scan --cache 純離線用)。"""
    end = end or (date.today() - timedelta(days=1))
    got: list[date] = []
    cur = end
    scanned = 0
    while len(got) < days and scanned < MAX_LOOKBACK_CAL_DAYS:
        scanned += 1
        if cur.weekday() < 5:              # 先跳過週末(省請求)；例假仍靠端點回空判定
            if offline:
                if load_day_cache(cur):
                    got.append(cur)
            else:
                was_cached = _day_file(cur).exists()
                data = ensure_day(cur)
                if data:
                    got.append(cur)
                    if not was_cached:
                        time.sleep(THROTTLE_SEC)   # 只在真的連網抓到才節流(命中快取不延遲)
        cur -= timedelta(days=1)
    return got


def backfill(days: int = DEFAULT_DAYS, end: date | None = None) -> list[date]:
    """回補最近 N 交易日(冷啟動)/增量(已快取者跳過)。回傳收齊的交易日(新→舊)。"""
    td = recent_trading_days(days, end)
    print(f"[chips] 已備妥 {len(td)} 個交易日："
          f"{', '.join(d.isoformat() for d in td) if td else '(無)'}")
    return td


# ── 對外：載入近 N 交易日，聚合每檔籌碼 ─────────────────────────────────────
def load_chips(codes=None, days: int = DEFAULT_DAYS,
               end: date | None = None, offline: bool = False) -> dict[str, dict]:
    """回傳 {code: {foreign_net, trust_net, instinv_net, consec_buy_days, t_minus, date}}。
      - foreign/trust/instinv_net：最近一個交易日值(單位張)。
      - consec_buy_days：外資+投信『合計』從最近交易日往回連續淨買(>0)的天數。
      - net_sum_n：近 N 交易日 外資+投信合計淨買加總(供 scan 判『近N日淨買』)。
      - t_minus：相對「最近交易日」的位移(此處恆 0；盤中由 scan 改標 1)。
      - date：該檔最近一筆籌碼的交易日。
    codes 給定時只回傳交集(順便濾掉 ETF/權證——它們不在精選/全市場『股票』宇宙)。
    offline=True：只讀本地快取、絕不連網(給 scan --cache)。"""
    td = recent_trading_days(days, end, offline=offline)    # 新→舊
    if not td:
        return {}
    per_day: list[tuple[date, dict[str, dict]]] = []
    for d in td:
        data = load_day_cache(d) if offline else (load_day_cache(d) or ensure_day(d))
        if data:
            per_day.append((d, data))
    if not per_day:
        return {}

    code_set = set(codes) if codes else None
    latest_date = per_day[0][0]
    # 收齊所有出現過的代號(或限定 codes)
    all_codes: set[str] = set()
    for _, data in per_day:
        all_codes.update(data.keys())
    if code_set is not None:
        all_codes &= code_set

    out: dict[str, dict] = {}
    for code in all_codes:
        latest = per_day[0][1].get(code)
        if latest is None:
            # 最近交易日該檔無資料(可能當日無交易)；以較舊一日補基準值
            latest = next((data[code] for _, data in per_day if code in data), None)
            if latest is None:
                continue
        # 連買天數 + 近 N 日加總(外資+投信合計 / 投信單獨)
        consec = 0
        trust_consec = 0
        broke = False
        trust_broke = False
        net_sum = 0
        trust_net_sum = 0
        for _, data in per_day:
            rec = data.get(code)
            if rec is None:
                broke = True              # 該日無此檔資料 → 連買中斷判定停止
                trust_broke = True
            else:
                ft = rec["foreign_net"] + rec["trust_net"]
                t = rec["trust_net"]
                net_sum += ft
                trust_net_sum += t
                if not broke and ft > 0:
                    consec += 1
                elif not broke:
                    broke = True
                if not trust_broke and t > 0:
                    trust_consec += 1
                elif not trust_broke:
                    trust_broke = True
        out[code] = {
            "foreign_net": latest["foreign_net"],
            "trust_net": latest["trust_net"],
            "instinv_net": latest["instinv_net"],
            "consec_buy_days": consec,
            "trust_consec_days": trust_consec,   # 投信單獨連買天數(阿斯匹靈法近10日投信超)
            "net_sum_n": net_sum,
            "trust_net_sum": trust_net_sum,      # 近 N 日投信淨買加總(張)
            "t_minus": 0,
            "date": latest_date.isoformat(),
        }
    return out


def latest_chip_date(days: int = DEFAULT_DAYS, end: date | None = None) -> str | None:
    td = recent_trading_days(days, end)
    return td[0].isoformat() if td else None


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS, help="回補交易日數")
    ap.add_argument("--date", type=str, default=None, help="只抓指定日(YYYYMMDD)")
    ap.add_argument("--show", type=str, default=None, help="顯示某代號近 N 日籌碼")
    ap.add_argument("--force", action="store_true", help="忽略快取重抓")
    args = ap.parse_args()

    if args.date:
        d = datetime.strptime(args.date, "%Y%m%d").date()
        data = ensure_day(d, force=args.force)
        if data is None:
            print(f"[chips] {d.isoformat()} 非交易日或無資料")
        else:
            print(f"[chips] {d.isoformat()} 取得 {len(data)} 檔(上市+上櫃，已快取)")
            for c in ("2330", "2317", "5483"):
                if c in data:
                    r = data[c]
                    print(f"    {c}: 外資{r['foreign_net']:+}張 投信{r['trust_net']:+}張 "
                          f"三大法人{r['instinv_net']:+}張")
        return

    td = backfill(args.days)
    if args.show:
        chips = load_chips([args.show], days=args.days)
        rec = chips.get(args.show)
        if rec:
            print(f"[chips] {args.show} 近{args.days}日："
                  f"外資{rec['foreign_net']:+}張 投信{rec['trust_net']:+}張 "
                  f"三大法人{rec['instinv_net']:+}張 連買{rec['consec_buy_days']}日 "
                  f"(近N日合計{rec['net_sum_n']:+}張，as of {rec['date']})")
        else:
            print(f"[chips] {args.show} 近{args.days}日無資料")


if __name__ == "__main__":
    main()
