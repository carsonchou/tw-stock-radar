# -*- coding: utf-8 -*-
"""
_util.py — 測試共用工具(非測試檔，unittest discover 不會挑到 test*.py 以外)。

負責：
  1. 把 quant-service/ 與 data_hunter/ 掛上 sys.path，讓 scan/chips/margin/tdcc/
     track/indicators 皆可被測試匯入(不改任何生產碼)。
  2. 提供合成 K 線 DataFrame 產生器(不連網、純合成資料)。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── 路徑掛載(讓生產模組可匯入) ──────────────────────────────────────────────
HERE = Path(__file__).resolve().parent          # tests/
DH = HERE.parent                                 # data_hunter/
QS = DH.parent                                   # quant-service/
for p in (str(QS), str(DH)):
    if p not in sys.path:
        sys.path.insert(0, p)


def make_df(closes, hi_frac: float = 0.005, lo_frac: float = 0.005,
            vol=None, start: str = "2025-01-01") -> pd.DataFrame:
    """由收盤序列合成 OHLCV DataFrame(大寫欄位，DatetimeIndex，工作日)。

    open = 前一根收盤(首根=自身)；high/low 由 open/close 外擴一個小比例，
    確保 high>=max(open,close)、low<=min(open,close)，符合真實 K 線約束。
    """
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    vol = np.full(n, 1000.0) if vol is None else np.asarray(vol, dtype=float)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + hi_frac)
    lows = np.minimum(opens, closes) * (1 - lo_frac)
    idx = pd.date_range(start, periods=n, freq="B")
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": vol},
        index=idx,
    )


def rising_with_pullbacks(n: int = 40, start: float = 100.0,
                          up: float = 1.02, dip: float = 0.995) -> list[float]:
    """上升趨勢但每 5 根一次小回檔(讓 RSI 有 loss 分母、不至於除零成 NaN)。"""
    out, p = [], start
    for i in range(n):
        p *= dip if i % 5 == 0 else up
        out.append(p)
    return out


def falling_with_bounces(n: int = 40, start: float = 100.0,
                         dn: float = 0.98, bounce: float = 1.005) -> list[float]:
    """下降趨勢但每 5 根一次小反彈(對稱於 rising_with_pullbacks)。"""
    out, p = [], start
    for i in range(n):
        p *= bounce if i % 5 == 0 else dn
        out.append(p)
    return out


def long_flip_df():
    """回傳會觸發『做多(SuperTrend 翻多+量能放大)』訊號的 DataFrame。
    40 根緩降(100→85)壓成 SuperTrend 空方，最後一根 +6% 反彈翻多、末根放量 3x。"""
    base = list(np.linspace(100, 85, 40))
    closes = base + [base[-1] * 1.06]
    vol = [1000.0] * 40 + [3000.0]
    return make_df(closes, vol=vol)


def short_st_flip_df():
    """回傳會觸發『做空(SuperTrend 翻空)』訊號的 DataFrame。
    40 根緩升(85→100)壓成多方，最後一根 -6% 急殺翻空。"""
    base = list(np.linspace(85, 100, 40))
    closes = base + [base[-1] * 0.94]
    return make_df(closes)


def short_ma_break_df():
    """回傳會觸發『做空(跌破 20MA，但 SuperTrend 未翻空)』訊號的 DataFrame。
    先緩升站上均線 → 尾段走平 → 最後一根 -2% 跌破 20MA。"""
    base = list(np.linspace(90, 100, 30)) + [100.0] * 10
    closes = base + [base[-1] * 0.98]
    return make_df(closes)


def no_chase_long_df():
    """做多型態但末根 +10%(觸漲停附近 no_chase)，應被排除進場(signal=None)。"""
    base = list(np.linspace(100, 85, 40))
    closes = base + [base[-1] * 1.10]
    vol = [1000.0] * 40 + [3000.0]
    return make_df(closes, vol=vol)
