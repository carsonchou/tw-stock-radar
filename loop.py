# -*- coding: utf-8 -*-
"""
loop.py — 數據獵手背景掃描迴圈

每 N 分鐘掃一輪、更新 state.json、推新訊號。
盤中(09:00-13:35 台股交易時段)用較短間隔即時刷新；非盤中拉長省資源。

用法：
  python loop.py                 # 預設盤中 5 分、盤後 30 分
  python loop.py --interval 5    # 固定每 5 分鐘
  python loop.py --once          # 只跑一次(等同 scan.py)
"""
from __future__ import annotations

import argparse
import time
from datetime import datetime

import scan


def _is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:           # 週末
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 <= hm <= 13 * 60 + 35   # 09:00-13:35


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=int, default=0,
                    help="固定間隔(分鐘)；0=盤中5分/盤後30分自動")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--no-push", action="store_true")
    args = ap.parse_args()

    if args.once:
        scan.run_once(push=not args.no_push)
        return

    print("[loop] 數據獵手背景掃描啟動（Ctrl+C 停止）")
    from datetime import date as _date
    zones_day = None
    while True:
        mh = _is_market_hours()
        try:
            # 盤中走 15 分 K 即時、盤後走日線(穩定有資料)
            scan.run_once(push=not args.no_push, cache_only=False, intraday=mh)
            if zones_day != _date.today():           # 每日一次全市場交易專區
                try:
                    import zones
                    zones.build_zones(full=True, use_cache_only=True)
                    zones_day = _date.today()
                except Exception as e:
                    print(f"[loop] 交易專區略過：{type(e).__name__}: {e}")
        except Exception as e:
            print(f"[loop] 本輪錯誤（續跑）：{type(e).__name__}: {e}")
        wait = args.interval if args.interval > 0 else (5 if mh else 30)
        nxt = datetime.now().strftime("%H:%M:%S")
        print(f"[loop] {nxt} 本輪結束，{wait} 分鐘後再掃（{'盤中' if mh else '盤後'}）\n")
        time.sleep(wait * 60)


if __name__ == "__main__":
    main()
