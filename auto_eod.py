# -*- coding: utf-8 -*-
"""
auto_eod.py — 數據獵手「每日自動」常駐排程

開機自動啟動後就掛著：每個交易日(週一~五)到設定時間(預設 17:00，
確保三大法人/融資券籌碼盤後都出齊)自動跑一次完整 eod.py
(刷快取→四維籌碼→掃描→產IG/YT貼文+海報→推 ntfy)，你完全不用手動。

為什麼 17:00：台股 13:30 收盤，但三大法人(T86)/融資券(MI_MARGN)是盤後傍晚才
公布，太早跑籌碼會抓不到當日值。17:00 通常都出齊了。

不需要 Windows 工作排程(那步會被安全擋)；這支自己 sleep-until 迴圈，
關掉視窗就停。搭配開機捷徑=開機自動跑。

用法：
  python auto_eod.py            # 常駐，每交易日 17:00 自動跑 eod(含推播)
  python auto_eod.py --at 15:30 # 自訂每日執行時間
  python auto_eod.py --now      # 立刻跑一次(測試)後繼續常駐
  python auto_eod.py --no-push  # 自動跑時不推 ntfy
"""
from __future__ import annotations

import sys
import time
import argparse
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYEXE = sys.executable
DEFAULT_AT = "17:00"


def _log(msg: str) -> None:
    print(f"[auto] {datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def _next_run(at_h: int, at_m: int) -> datetime:
    """回傳下一個『交易日(週一~五) at_h:at_m』的時間點。"""
    now = datetime.now()
    cand = now.replace(hour=at_h, minute=at_m, second=0, microsecond=0)
    if cand <= now:
        cand += timedelta(days=1)
    while cand.weekday() >= 5:          # 週末往後推到週一
        cand += timedelta(days=1)
    return cand


def _run_eod(push: bool) -> None:
    cmd = [PYEXE, str(HERE / "eod.py")]
    if push:
        cmd.append("--push")
    _log(f"開始跑 eod：{' '.join(cmd[-2:])}")
    try:
        r = subprocess.run(cmd, cwd=str(HERE), timeout=1800)
        _log(f"eod 結束（return={r.returncode}）")
    except Exception as e:
        _log(f"eod 執行例外（續掛）：{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default=DEFAULT_AT, help="每日執行時間 HH:MM(預設17:00)")
    ap.add_argument("--now", action="store_true", help="啟動時立刻跑一次(測試)")
    ap.add_argument("--no-push", action="store_true", help="自動跑時不推 ntfy")
    args = ap.parse_args()
    try:
        at_h, at_m = (int(x) for x in args.at.split(":"))
    except Exception:
        _log(f"--at 格式錯誤（要 HH:MM），改用預設 {DEFAULT_AT}")
        at_h, at_m = 17, 0
    push = not args.no_push

    _log(f"數據獵手每日自動排程啟動：每交易日 {at_h:02d}:{at_m:02d} 跑 eod"
         f"{'（含推播）' if push else '（不推播）'}｜關視窗即停")
    if args.now:
        _run_eod(push)

    while True:
        nxt = _next_run(at_h, at_m)
        wait = (nxt - datetime.now()).total_seconds()
        _log(f"下次自動執行：{nxt:%Y-%m-%d(%a) %H:%M}（{wait/3600:.1f} 小時後）")
        # 分段 sleep，避免長睡被系統休眠打亂；每 30 分醒來校時
        while True:
            remain = (nxt - datetime.now()).total_seconds()
            if remain <= 0:
                break
            time.sleep(min(remain, 1800))
        _run_eod(push)
        time.sleep(60)                  # 避免同分鐘重觸


if __name__ == "__main__":
    main()
