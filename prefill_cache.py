"""Fast parallel cache prefill — 全市場 1900+ 檔

策略(優先序):
  1. twstock 官方(上市/上櫃皆正確)
  2. TWSE STOCK_DAY 直接 API(上市)
  3. TPEX 單檔月線 API(上櫃)
  4. yfinance 個別 fallback

執行:
  python prefill_cache.py          # 只補缺的
  python prefill_cache.py --all    # 強制重抓全部
  python prefill_cache.py --missing-only  # 僅補 twstock 沒抓到的(Phase 2)
"""
from __future__ import annotations
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))

CACHE_DIR = Path(__file__).resolve().parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

import scan
import twse_price as _tp
try:
    import twstock
    _HAS_TWSTOCK = True
except ImportError:
    _HAS_TWSTOCK = False

# ── 取得宇宙清單及市場別 ────────────────────────────────────────────────────
rows = scan.load_full_universe()
codes_all = [c for c, _, _ in rows]

def _market(code: str) -> str:
    if _HAS_TWSTOCK:
        info = twstock.codes.get(code)
        if info:
            return info.market  # '上市' or '上櫃'
    return "上市"  # default

# ── Phase 1: twstock 並行(速度快，覆蓋率~85%) ─────────────────────────────
force_all = "--all" in sys.argv
missing_only = "--missing-only" in sys.argv

existing = {p.stem.split("_")[0] for p in CACHE_DIR.glob("*.csv")}

if missing_only:
    todo_phase1 = []
else:
    todo_phase1 = codes_all if force_all else [c for c in codes_all if c not in existing]

print(f"Total: {len(codes_all)} | Cached: {len(existing)} | Phase-1 todo: {len(todo_phase1)}", flush=True)

written_p1 = 0

def fetch_twstock(code: str) -> bool:
    try:
        df = _tp.fetch_twstock_daily(code, months_back=9)
        if df is not None and len(df) >= 5:
            mkt = _market(code)
            suf = "_TW.csv" if mkt == "上市" else "_TWO.csv"
            df.to_csv(CACHE_DIR / f"{code}{suf}")
            time.sleep(0.25)
            return True
    except Exception:
        pass
    time.sleep(0.25)
    return False

WORKERS = 4
batch_size = 100
for i in range(0, len(todo_phase1), batch_size):
    batch = todo_phase1[i:i + batch_size]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(fetch_twstock, c) for c in batch]
        for f in as_completed(futs):
            if f.result():
                written_p1 += 1
    total = len({p.stem.split("_")[0] for p in CACHE_DIR.glob("*.csv")})
    print(f"Phase-1 {i + len(batch)}/{len(todo_phase1)} | cached: {total}", flush=True)

# ── Phase 2: TWSE/TPEX 直接 API 補缺口 ────────────────────────────────────
existing2 = {p.stem.split("_")[0] for p in CACHE_DIR.glob("*.csv")}
still_missing = [(c, _market(c)) for c in codes_all if c not in existing2]

print(f"\nPhase-2 補缺: {len(still_missing)} 檔 (TWSE/TPEX 直接 API + yfinance fallback)", flush=True)

if still_missing:
    written_p2 = _tp.rebuild_missing(still_missing, months=9, sleep=0.5, verbose=True,
                                     cache_dir=CACHE_DIR)
    print(f"Phase-2 完成: 補上 {written_p2}/{len(still_missing)} 檔", flush=True)
else:
    written_p2 = 0
    print("Phase-2: 無缺口，略過", flush=True)

# ── 最終統計 ───────────────────────────────────────────────────────────────
final = len({p.stem.split("_")[0] for p in CACHE_DIR.glob("*.csv")})
print(f"\nDone. Phase-1新增: {written_p1} | Phase-2補缺: {written_p2} | Total cache: {final}", flush=True)
