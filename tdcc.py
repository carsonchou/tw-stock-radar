# -*- coding: utf-8 -*-
"""
tdcc.py — TDCC 集保戶數週資料(台灣集中保管結算所)

每週抓一次各股股東人數分布(依持股張數分 16 級)，追蹤小股東(散戶)戶數週變化，
作為主力吸籌/散戶進出的輔助訊號(與三大法人 chips.py 互補)。

資料來源
  https://www.tdcc.com.tw/smWeb/QryStockAjax.do
  POST encDate=YYYYMMDD & stockNo=代號
  每個 encDate 對應一個「持股統計基準日」(TDCC 每週公布，通常以每週五截止日為 key)

持股分布 16 級(股)
  1: 1-999    2: 1,000-5,000    3: 5,001-10,000
  4: 10,001-15,000  5: 15,001-20,000  6: 20,001-30,000
  7: 30,001-40,000  8: 40,001-50,000  9: 50,001-100,000
  10: 100,001-200,000  11: 200,001-400,000  12: 400,001-600,000
  13: 600,001-800,000  14: 800,001-1,000,000  15: 1,000,001+  16: 合計
  小散戶(持 1-10 張)= 1-10,000 股 = 級 1+2+3

訊號邏輯
  small_chg_pct < SMALL_CHG_WARN(預設-2%) + 股價上漲 → 散戶流出/主力吸籌(做多加分)
  small_chg_pct > SMALL_ENTRY_THR(預設+3%) + 股價上漲 → 散戶大舉進場(過熱警示)

快取
  twdata/tdcc/{YYYYMMDD}.json  (每個調查日一檔，存全部當日查詢的代號)

用法
  python tdcc.py                   # 抓最新週資料(精選 ~130 檔)
  python tdcc.py --date 20260620   # 抓指定 encDate
  python tdcc.py --code 2330       # 只抓特定代號
  python tdcc.py --show 2330       # 顯示近 2 週變化
  python tdcc.py --probe           # 只探測最近有效 encDate(不下載)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
QS = HERE.parent
ROOT = QS.parent
TDCC_DIR = ROOT / "twdata" / "tdcc"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TDCC_URL = "https://www.tdcc.com.tw/smWeb/QryStockAjax.do"
TDCC_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36 DataHunter/1.0"),
    "Referer": "https://www.tdcc.com.tw/portal/zh/smWeb/qryStock",
    "Origin": "https://www.tdcc.com.tw",
    "Content-Type": "application/x-www-form-urlencoded",
    "Accept": "application/json, text/html, */*",
}
THROTTLE_SEC = 3.0          # 每次請求間隔(節流，避免被 TDCC 擋)
PROBE_CODE = "2330"         # 探測用代號(台積電，流通性佳一定有資料)
SMALL_GRADE_MAX = 3         # 小散戶分級上限(grade 1-3 = 持 1-10,000 股 = 1-10 張)
SMALL_CHG_WARN = -2.0       # 小股東戶數週縮 ≥此% → retail_exit=True(散戶流出訊號)
SMALL_ENTRY_THR = 3.0       # 小股東戶數週增 ≥此% → retail_surge=True(散戶擁入過熱)
MAX_PROBE_DAYS = 30         # 探測最近有效日最多往回查幾個日曆日


# ── 通用工具 ────────────────────────────────────────────────────────────────
def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _to_int(raw) -> int:
    """去逗號、空格，轉 int；失敗回 0。"""
    if raw is None:
        return 0
    s = str(raw).strip().replace(",", "").replace(" ", "")
    if s in ("", "-", "--"):
        return 0
    try:
        return int(float(s))
    except (ValueError, TypeError):
        return 0


# ── 單筆 API 查詢 ──────────────────────────────────────────────────────────
def fetch_stock(code: str, enc_date: str) -> dict | None:
    """向 TDCC 查詢 code 在 enc_date 的股東分布。
    回傳 {"small_count": N, "total_count": N} 或 None(無資料/失敗)。
    注意：TDCC API 路徑若變動，請更新 TDCC_URL 常數。"""
    try:
        import requests
    except ImportError:
        print("[tdcc] requests 未安裝，請 pip install requests")
        return None

    payload = {"encDate": enc_date, "stockNo": code}
    try:
        r = requests.post(TDCC_URL, data=payload, headers=TDCC_HEADERS, timeout=25)
        if r.status_code != 200:
            return None
        # 嘗試 JSON 解析(新版 API)
        try:
            j = r.json()
            result = _parse_json(j)
            if result:
                return result
        except Exception:
            pass
        # 回退 HTML 解析(舊版 API / 備援)
        return _parse_html(r.text)
    except Exception as e:
        print(f"[tdcc] fetch_stock {code} {enc_date} 失敗：{type(e).__name__}: {e}")
        return None


def _parse_json(j: dict) -> dict | None:
    """解析 JSON 格式：{"aaData": [[grade, max_shares, count, shares, pct], ...]}。
    grade 16 = 合計行；small = grade 1+2+3 戶數合計。"""
    rows = j.get("aaData") or []
    if not rows:
        return None
    small_count = 0
    total_count = 0
    for row in rows:
        try:
            grade = int(str(row[0]).strip())
        except (IndexError, ValueError):
            continue
        count = _to_int(row[2]) if len(row) > 2 else 0
        if grade <= SMALL_GRADE_MAX:
            small_count += count
        if grade == 16:         # 合計行
            total_count = count
    if total_count == 0:        # 無合計 → 嘗試從 small+其餘推算不可靠，直接回 None
        return None
    return {"small_count": small_count, "total_count": total_count}


def _parse_html(html: str) -> dict | None:
    """HTML table 備援解析；從 <td> 序列擷取分級戶數。
    假設欄位順序：分級 | 上限股數 | 人數 | 股數 | 比例(%)
    與各行 TDCC 頁面版本對齊(欄位漂移時可能需要調整)。"""
    if not html or "encDate" in html or "查無資料" in html:
        return None
    # 找 <tbody> 內所有 <tr>
    tbody = re.search(r"<tbody[^>]*>(.*?)</tbody>", html, re.DOTALL)
    if not tbody:
        return None
    tds_all = re.findall(r"<td[^>]*>\s*(.*?)\s*</td>", tbody.group(1), re.DOTALL)
    # 每 5 欄為一列：grade / max / count / shares / pct
    rows = [tds_all[i:i + 5] for i in range(0, len(tds_all), 5)]
    small_count = 0
    total_count = 0
    for row in rows:
        if len(row) < 3:
            continue
        try:
            grade = int(re.sub(r"<[^>]+>", "", row[0]).strip())
        except (ValueError, TypeError):
            continue
        count = _to_int(re.sub(r"<[^>]+>", "", row[2]))
        if grade <= SMALL_GRADE_MAX:
            small_count += count
        if grade == 16:
            total_count = count
    if total_count == 0:
        return None
    return {"small_count": small_count, "total_count": total_count}


# ── 日期探測 + 快取 ──────────────────────────────────────────────────────────
def _date_file(enc_date: str) -> Path:
    return TDCC_DIR / f"{enc_date}.json"


def _load_date_cache(enc_date: str) -> dict[str, dict] | None:
    p = _date_file(enc_date)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return obj.get("data") or None
    except Exception:
        return None


def _find_available_dates(n: int = 2, offline: bool = False) -> list[str]:
    """往回探測，找 n 個最近有效的 TDCC encDate(YYYYMMDD，新→舊)。
    先查本地快取目錄有無已知日期，不足才實際探測 API(用 PROBE_CODE 試)。
    這樣冷啟動才多打 API，後續週更新只補差值。
    offline=True：只認本地快取，絕不探 API(給 scan --cache)。"""
    # Step 1：從本地快取找已知有效日期
    known: list[str] = []
    if TDCC_DIR.exists():
        for p in sorted(TDCC_DIR.glob("????????.json"), reverse=True):
            stem = p.stem
            if re.match(r"^\d{8}$", stem):
                known.append(stem)
        if len(known) >= n:
            return known[:n]

    if offline:                 # 離線：不探 API，有幾個算幾個(可能 <n，甚至空)
        return known[:n]

    # Step 2：API 探測(遍歷最近日曆日；TDCC 資料以非假日的任意日為 encDate)
    found = list(known)
    today = date.today()
    probed = 0
    for i in range(MAX_PROBE_DAYS):
        d = today - timedelta(days=i)
        if d.weekday() >= 5:        # 跳過週六日
            continue
        ymd = d.strftime("%Y%m%d")
        if ymd in found:
            continue
        probed += 1
        result = fetch_stock(PROBE_CODE, ymd)
        time.sleep(THROTTLE_SEC)
        if result:
            found.append(ymd)
            print(f"[tdcc] 發現有效 encDate：{ymd}")
            # 快取這個日期的探測結果(probe code 先進去)
            p = _date_file(ymd)
            if not p.exists():
                _atomic_write_json(p, {
                    "encDate": ymd,
                    "fetched": datetime.now().isoformat(timespec="seconds"),
                    "data": {PROBE_CODE: result},
                })
        if len(found) >= n:
            break
    if probed and len(found) < n:
        print(f"[tdcc] 警告：只找到 {len(found)}/{n} 個有效 encDate(探測 {probed} 個日期)")
    found.sort(reverse=True)
    return found[:n]


def ensure_date(enc_date: str, codes: list[str], offline: bool = False) -> dict[str, dict]:
    """抓 enc_date 的所有 codes 集保戶數，回傳並更新快取。
    已快取的代號直接命中，只補缺漏的。
    offline=True：只讀快取、絕不連網補抓(給 scan --cache)。"""
    cached = _load_date_cache(enc_date) or {}
    missing = [c for c in codes if c not in cached]
    if not missing or offline:
        return {c: cached[c] for c in codes if c in cached}

    print(f"[tdcc] {enc_date} 補抓 {len(missing)} 檔…")
    for i, code in enumerate(missing):
        if i > 0:
            time.sleep(THROTTLE_SEC)
        result = fetch_stock(code, enc_date)
        if result:
            cached[code] = result
        else:
            print(f"[tdcc]   {code} 無資料(可能非普通股或當日無持股紀錄)")

    # 回寫快取
    _atomic_write_json(_date_file(enc_date), {
        "encDate": enc_date,
        "fetched": datetime.now().isoformat(timespec="seconds"),
        "data": cached,
    })
    return {c: cached[c] for c in codes if c in cached}


# ── 對外主 API ───────────────────────────────────────────────────────────────
def load_tdcc(codes=None, offline: bool = False) -> dict[str, dict]:
    """載入最近 2 個有效 encDate 的集保戶數，計算週差值。
    回傳 {code: {small_count, small_count_prev, small_chg, small_chg_pct,
                 total_count, retail_exit, retail_surge, enc_date}}。
    缺資料 → 不在 dict 內(優雅降級；scan 以 None 處理)。
    offline=True：僅讀快取、絕不探測/連網 API；由 scan --cache 指令觸發。"""
    dates = _find_available_dates(n=2, offline=offline)
    if not dates:
        return {}
    latest_date = dates[0]
    prev_date = dates[1] if len(dates) > 1 else None

    # 如果沒傳 codes 就讀取精選宇宙
    if codes is None:
        try:
            sys.path.insert(0, str(HERE))
            from universe import all_codes as _ac
            codes = [c for c, _, _ in _ac()]
        except Exception:
            codes = []
    if not codes:
        return {}

    latest_data = ensure_date(latest_date, codes, offline=offline)
    prev_data = ensure_date(prev_date, codes, offline=offline) if prev_date else {}

    out: dict[str, dict] = {}
    for code in codes:
        cur = latest_data.get(code)
        if cur is None:
            continue
        sc = cur["small_count"]
        tc = cur["total_count"]
        prev = prev_data.get(code)
        sc_prev = prev["small_count"] if prev else None
        chg = (sc - sc_prev) if sc_prev is not None else None
        chg_pct = round((sc - sc_prev) / sc_prev * 100, 2) if sc_prev else None
        out[code] = {
            "small_count": sc,
            "small_count_prev": sc_prev,
            "small_chg": chg,
            "small_chg_pct": chg_pct,
            "total_count": tc,
            # 訊號旗標
            "retail_exit": bool(chg_pct is not None and chg_pct <= SMALL_CHG_WARN),
            "retail_surge": bool(chg_pct is not None and chg_pct >= SMALL_ENTRY_THR),
            "enc_date": latest_date,
        }
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None, help="只抓指定 encDate(YYYYMMDD)")
    ap.add_argument("--code", type=str, default=None, help="只抓特定代號")
    ap.add_argument("--show", type=str, default=None, help="顯示某代號近 2 週集保戶數")
    ap.add_argument("--probe", action="store_true", help="只探測最近有效 encDate(不下載)")
    args = ap.parse_args()

    if args.probe:
        dates = _find_available_dates(n=2)
        print(f"[tdcc] 最近有效 encDate：{dates}")
        return

    if args.show:
        tdcc = load_tdcc([args.show])
        rec = tdcc.get(args.show)
        if rec:
            print(f"[tdcc] {args.show} 集保戶數 ({rec['enc_date']})：")
            print(f"  小股東(1-10張)：{rec['small_count']:,} 人  "
                  f"前週：{rec['small_count_prev']:,} 人  "
                  f"週變化：{rec['small_chg']:+,}({rec['small_chg_pct']:+.1f}%)")
            print(f"  總股東：{rec['total_count']:,} 人  "
                  f"散戶流出：{rec['retail_exit']}  散戶擁入：{rec['retail_surge']}")
        else:
            print(f"[tdcc] {args.show} 無資料")
        return

    # 一般模式：抓精選宇宙 or 指定代號的最新週資料
    codes = [args.code] if args.code else None
    enc_date = args.date

    if enc_date:
        target_codes = codes
        if target_codes is None:
            try:
                sys.path.insert(0, str(HERE))
                from universe import all_codes as _ac
                target_codes = [c for c, _, _ in _ac()]
            except Exception:
                target_codes = []
        data = ensure_date(enc_date, target_codes or [])
        print(f"[tdcc] {enc_date} 取得 {len(data)} 檔集保戶數")
    else:
        tdcc = load_tdcc(codes)
        print(f"[tdcc] 取得 {len(tdcc)} 檔近週集保戶數")
        # 印摘要：小股東流出 top 5
        exits = [(c, r) for c, r in tdcc.items() if r.get("retail_exit")]
        if exits:
            exits.sort(key=lambda x: x[1]["small_chg_pct"])
            print(f"[tdcc] 散戶流出(小股東戶數↓ ≤{SMALL_CHG_WARN}%)共 {len(exits)} 檔，前 5：")
            for c, r in exits[:5]:
                print(f"  {c}  {r['small_chg_pct']:+.1f}%  "
                      f"({r['small_count']:,} ← {r['small_count_prev']:,})")


if __name__ == "__main__":
    main()
