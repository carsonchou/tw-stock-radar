# -*- coding: utf-8 -*-
"""
test_scan_signals.py — 掃描引擎(data_hunter/scan.py)訊號與計分邏輯。

涵蓋：
  _st_dirs      今日/昨日 SuperTrend 方向
  _analyse_core 強弱分四維(0-100 界內)
  analyse_one   合成 K 線的訊號(SuperTrend 翻多 / 翻空 / 跌破20MA)、no_chase 排除、
                停損停利 R 倍數數學(stop=close-3.5*ATR22、tp1=+1.5R、tp2=+4.5R)
全程純合成資料、不連網。
"""
import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
from _util import (make_df, long_flip_df, short_st_flip_df,             # noqa: E402
                   short_ma_break_df, no_chase_long_df)

import scan  # noqa: E402


class TestStDirs(unittest.TestCase):
    def _prep(self, df):
        return scan._lower(df.tail(180).reset_index(drop=True))

    def test_clean_uptrend_both_up(self):
        df = make_df(list(np.linspace(80, 120, 30)))
        self.assertEqual(scan._st_dirs(self._prep(df)), ("UP", "UP"))

    def test_clean_downtrend_both_down(self):
        df = make_df(list(np.linspace(120, 80, 30)))
        self.assertEqual(scan._st_dirs(self._prep(df)), ("DOWN", "DOWN"))

    def test_too_short_returns_none(self):
        df = make_df(list(range(100, 105)))
        self.assertEqual(scan._st_dirs(self._prep(df)), (None, None))


class TestScore(unittest.TestCase):
    def setUp(self):
        scan._ANALYSE_MEMO.clear()

    def test_score_within_bounds(self):
        """任意輸入 score 皆須落在 0-100。"""
        for closes in (list(np.linspace(80, 120, 40)),
                       list(np.linspace(120, 80, 40)),
                       [100.0] * 40):
            scan._ANALYSE_MEMO.clear()
            r = scan.analyse_one(make_df(closes), drop_last=False)
            self.assertIsNotNone(r)
            self.assertGreaterEqual(r["score"], 0.0)
            self.assertLessEqual(r["score"], 100.0)

    def test_uptrend_scores_higher_than_downtrend(self):
        """強勢上升(ST多+RSI高+%B高)分數應顯著高於下降。"""
        scan._ANALYSE_MEMO.clear()
        up = scan.analyse_one(make_df(_util.rising_with_pullbacks(n=60)), drop_last=False)
        scan._ANALYSE_MEMO.clear()
        dn = scan.analyse_one(make_df(_util.falling_with_bounces(n=60)), drop_last=False)
        self.assertGreater(up["score"], dn["score"])


class TestSignals(unittest.TestCase):
    def setUp(self):
        scan._ANALYSE_MEMO.clear()

    def test_long_signal_supertrend_flip(self):
        r = scan.analyse_one(long_flip_df(), drop_last=False)
        self.assertEqual(r["signal"], "long")
        self.assertEqual(r["st"], "UP")
        self.assertTrue(r["firm"])                 # 末根放量 3x → firm
        self.assertFalse(r["no_chase"])
        self.assertTrue(30 <= r["rsi"] <= 70)

    def test_short_signal_supertrend_flip(self):
        scan._ANALYSE_MEMO.clear()
        r = scan.analyse_one(short_st_flip_df(), drop_last=False)
        self.assertEqual(r["signal"], "short")
        self.assertEqual(r["st"], "DOWN")
        self.assertIn("SuperTrend", r["reason"])

    def test_short_signal_ma_break(self):
        """跌破 20MA 但 SuperTrend 尚未翻空 → short 且 st 仍 UP。"""
        scan._ANALYSE_MEMO.clear()
        r = scan.analyse_one(short_ma_break_df(), drop_last=False)
        self.assertEqual(r["signal"], "short")
        self.assertEqual(r["st"], "UP")
        self.assertIn("20MA", r["reason"])
        self.assertFalse(r["above20"])

    def test_no_chase_suppresses_long(self):
        """末根漲幅 >=9.5% → no_chase，做多訊號被排除(signal=None)。"""
        scan._ANALYSE_MEMO.clear()
        r = scan.analyse_one(no_chase_long_df(), drop_last=False)
        self.assertTrue(r["no_chase"])
        self.assertIsNone(r["signal"])


class TestStopTargetMath(unittest.TestCase):
    def setUp(self):
        scan._ANALYSE_MEMO.clear()

    def test_long_stop_tp_r_multiples(self):
        """做多：R=price-stop；tp1≈price+1.5R；tp2≈price+4.5R。"""
        r = scan.analyse_one(long_flip_df(), drop_last=False)
        price, stop, tp1, tp2 = r["price"], r["stop"], r["tp1"], r["tp2"]
        self.assertLess(stop, price)             # 停損在下方
        self.assertGreater(tp1, price)           # 停利在上方
        R = price - stop
        self.assertAlmostEqual(tp1, price + 1.5 * R, delta=0.1)
        self.assertAlmostEqual(tp2, price + 4.5 * R, delta=0.1)

    def test_short_stop_tp_r_multiples(self):
        """做空：R=stop-price；tp1≈price-1.5R；tp2≈price-4.5R。"""
        scan._ANALYSE_MEMO.clear()
        r = scan.analyse_one(short_st_flip_df(), drop_last=False)
        price, stop, tp1, tp2 = r["price"], r["stop"], r["tp1"], r["tp2"]
        self.assertGreater(stop, price)          # 做空停損在上方
        self.assertLess(tp1, price)
        R = stop - price
        self.assertAlmostEqual(tp1, price - 1.5 * R, delta=0.1)
        self.assertAlmostEqual(tp2, price - 4.5 * R, delta=0.1)


if __name__ == "__main__":
    unittest.main()
