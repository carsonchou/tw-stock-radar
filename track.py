# -*- coding: utf-8 -*-
"""
track.py — 數據獵手「訊號命中率回灌」(#1)

把每個『已確認(confirmed)』推出的訊號持久化到 signals_book.json，事後用 twdata/cache
的後續日線 OHLC 客觀判定出場戰績，聚合成 state["track"] 回灌看板。讓「會不會賺」不再靠感覺。

出場判定(每筆從 entry_date 隔日起逐根走)：
  做多：盤中 low<=stop → 停損 loss(同根若也碰 tp1，保守取停損先觸)；high>=tp1 → 停利 win；
        滿 TIME_STOP_BARS 根未觸 → time-stop，以該根收盤結算(報酬正=win/負=loss)。
  做空：對稱(high>=stop→loss、low<=tp1→win)。
  後續資料不足評估期且未觸停損停利 → 仍 open，以最後一根收盤標『未實現』報酬(exit_reason=open)。
每筆算 ret_pct 與 R 倍數(R=|entry−stop|)。只讀快取、不連網、不下單。

去重：同 (entry_date, code, side) 不重複記。signals_book.json 原子寫入。
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scan   # 重用 _read_cache / _atomic_write_json / HERE

BOOK_FILE = HERE / "signals_book.json"
TIME_STOP_BARS = 15        # 滿 15 個交易日未觸停損停利 → time-stop 結算
RECENT_N = 10              # state["track"].recent 最多筆數


# ── 持久化：記錄本輪新確認訊號 ──────────────────────────────────────────────
def _load_book() -> list[dict]:
    if BOOK_FILE.exists():
        try:
            import json
            obj = json.loads(BOOK_FILE.read_text(encoding="utf-8"))
            return obj.get("trades", [])
        except Exception:
            return []
    return []


def record_signals(state: dict, book: list[dict]) -> int:
    """把 state 內『已確認』訊號(long+short)加入 book(去重)。回傳新增筆數。"""
    if not state.get("ok") or not state.get("confirmed"):
        return 0                 # 盤中候選不記
    seen = {(t["entry_date"], t["code"], t["side"]) for t in book}
    entry_date = state["date"]
    added = 0
    for side in ("long", "short"):
        for s in state["signals"][side]:
            if not s.get("confirmed"):
                continue
            k = (entry_date, s["code"], s["side"])
            if k in seen:
                continue
            seen.add(k)
            book.append({
                "code": s["code"], "name": s["name"], "side": s["side"],
                "entry_date": entry_date, "entry": s["price"],
                "stop": s.get("stop"), "tp1": s.get("tp1"), "tp2": s.get("tp2"),
                "status": "open",
            })
            added += 1
    return added


# ── 事後評估：用快取 OHLC 判定出場 ──────────────────────────────────────────
def evaluate(trade: dict) -> dict:
    """回傳補上戰績欄位的 trade 副本(status/result/exit_date/exit/ret_pct/r/exit_reason)。"""
    t = dict(trade)
    entry = t.get("entry")
    stop = t.get("stop")
    tp1 = t.get("tp1")
    side = t.get("side")
    if entry is None or stop is None or tp1 is None:
        t["status"] = "open"; t["result"] = "open"; t["exit_reason"] = "no_levels"
        t["exit"] = entry; t["ret_pct"] = 0.0; t["r"] = 0.0
        return t

    df = scan._read_cache(t["code"])
    R = abs(entry - stop)
    if df is None or R <= 0:
        t["status"] = "open"; t["result"] = "open"; t["exit_reason"] = "open"
        t["exit_date"] = t["entry_date"]; t["exit"] = entry; t["ret_pct"] = 0.0; t["r"] = 0.0
        return t

    df = df.rename(columns={c: c.lower() for c in df.columns})
    # errors="coerce"：快取偶有壞日期列(如 index="8")，一列壞不可拖垮整個回灌 → 該列丟棄
    df.index = pd.to_datetime(df.index, errors="coerce").normalize()
    df = df[df.index.notna()]
    if df.empty:
        t["status"] = "open"; t["result"] = "open"; t["exit_reason"] = "open"
        t["exit_date"] = t["entry_date"]; t["exit"] = entry; t["ret_pct"] = 0.0; t["r"] = 0.0
        return t
    after = df[df.index > pd.Timestamp(t["entry_date"]).normalize()]

    def _ret(exit_px: float) -> tuple[float, float]:
        pnl = (exit_px - entry) if side == "long" else (entry - exit_px)
        return round(pnl / entry * 100, 2), round(pnl / R, 2)

    if len(after) == 0:
        t["status"] = "open"; t["result"] = "open"; t["exit_reason"] = "open"
        t["exit_date"] = t["entry_date"]; t["exit"] = entry
        t["ret_pct"], t["r"] = 0.0, 0.0
        return t

    for i, (idx, row) in enumerate(after.iterrows()):
        hi, lo, cl = float(row["high"]), float(row["low"]), float(row["close"])
        ex_date = str(idx.date())
        if side == "long":
            if lo <= stop:                      # 保守：同根先觸停損
                rp, rr = _ret(stop)
                return {**t, "status": "closed", "result": "loss", "exit_reason": "stop",
                        "exit_date": ex_date, "exit": round(stop, 2), "ret_pct": rp, "r": rr}
            if hi >= tp1:
                rp, rr = _ret(tp1)
                return {**t, "status": "closed", "result": "win", "exit_reason": "tp1",
                        "exit_date": ex_date, "exit": round(tp1, 2), "ret_pct": rp, "r": rr}
        else:                                   # short 對稱
            if hi >= stop:
                rp, rr = _ret(stop)
                return {**t, "status": "closed", "result": "loss", "exit_reason": "stop",
                        "exit_date": ex_date, "exit": round(stop, 2), "ret_pct": rp, "r": rr}
            if lo <= tp1:
                rp, rr = _ret(tp1)
                return {**t, "status": "closed", "result": "win", "exit_reason": "tp1",
                        "exit_date": ex_date, "exit": round(tp1, 2), "ret_pct": rp, "r": rr}
        if i + 1 >= TIME_STOP_BARS:             # time-stop：以本根收盤結算
            rp, rr = _ret(cl)
            return {**t, "status": "closed",
                    "result": ("win" if rp > 0 else "loss"), "exit_reason": "time",
                    "exit_date": ex_date, "exit": round(cl, 2), "ret_pct": rp, "r": rr}

    # 評估期未滿且未觸停損停利 → open，以最後一根收盤標未實現
    last_idx = after.index[-1]
    last_cl = float(after.iloc[-1]["close"])
    rp, rr = _ret(last_cl)
    return {**t, "status": "open", "result": ("win" if rp > 0 else "loss"),
            "exit_reason": "open", "exit_date": str(last_idx.date()),
            "exit": round(last_cl, 2), "ret_pct": rp, "r": rr}


# ── 聚合 → state["track"] ───────────────────────────────────────────────────
def aggregate(evaluated: list[dict]) -> dict:
    closed = [t for t in evaluated if t["status"] == "closed"]
    opens = [t for t in evaluated if t["status"] != "closed"]
    n_closed, n_open = len(closed), len(opens)

    def _wr(subset: list[dict]) -> float | None:
        if not subset:
            return None
        return round(sum(1 for t in subset if t["result"] == "win") / len(subset), 3)

    win_rate = _wr(closed)
    avg_r = round(float(np.mean([t["r"] for t in closed])), 2) if closed else 0.0
    avg_ret = round(float(np.mean([t["ret_pct"] for t in closed])), 2) if closed else 0.0
    long_wr = _wr([t for t in closed if t["side"] == "long"])
    short_wr = _wr([t for t in closed if t["side"] == "short"])

    # recent：含 open+closed，依 entry_date 新到舊取前 N
    recent_src = sorted(evaluated, key=lambda t: (t.get("entry_date", ""),
                                                  t.get("exit_date", "")), reverse=True)[:RECENT_N]
    recent = [{"code": t["code"], "name": t["name"], "side": t["side"],
               "entry_date": t["entry_date"], "entry": t["entry"],
               "exit_date": t.get("exit_date"), "exit": t.get("exit"),
               "ret_pct": t.get("ret_pct"), "r": t.get("r"),
               "result": t.get("result"), "exit_reason": t.get("exit_reason")}
              for t in recent_src]

    return {
        "n_closed": n_closed, "n_open": n_open,
        "win_rate": win_rate if win_rate is not None else 0.0,
        "avg_r": avg_r, "avg_ret_pct": avg_ret,
        "long_win_rate": long_wr if long_wr is not None else 0.0,
        "short_win_rate": short_wr if short_wr is not None else 0.0,
        "updated": str(date.today()),
        "recent": recent,
    }


# ── 對外主流程：scan.run_once 每輪呼叫 ──────────────────────────────────────
def update(state: dict) -> dict:
    """記錄新確認訊號 → 重評估全簿 → 回寫 signals_book.json(原子) → 回傳 track 聚合。"""
    book = _load_book()
    record_signals(state, book)
    evaluated = [evaluate(t) for t in book]
    # 把評估後狀態回寫(持久化 status/result/exit)，原子寫入
    scan._atomic_write_json(BOOK_FILE, {"updated": str(date.today()), "trades": evaluated})
    return aggregate(evaluated)


if __name__ == "__main__":
    # 獨立檢視：印出目前簿子聚合
    bk = _load_book()
    ev = [evaluate(t) for t in bk]
    import json
    print(json.dumps(aggregate(ev), ensure_ascii=False, indent=2))
