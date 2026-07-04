# -*- coding: utf-8 -*-
"""
test_indicators.py — 技術指標(quant-service/indicators.py)性質檢定。

以合成資料檢查已知性質(不做逐點手算，改用單調/方向/範圍/一致性檢定)：
  RSI / MA / SuperTrend / MACD / Bollinger 對明確趨勢應給出對應方向與界內數值。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402  (掛路徑 + 合成資料)
from _util import make_df, rising_with_pullbacks, falling_with_bounces  # noqa: E402

from indicators import (calc_rsi, calc_ma, calc_supertrend,             # noqa: E402
                        calc_macd, calc_bollinger)


class TestRSI(unittest.TestCase):
    def test_rising_series_rsi_high(self):
        """上升趨勢 → RSI 偏高(>70，落 OVERBOUGHT)。"""
        df = make_df(rising_with_pullbacks())
        r = calc_rsi(df)
        self.assertIsNotNone(r["rsi"])
        self.assertGreater(r["rsi"], 70)
        self.assertEqual(r["signal"], "OVERBOUGHT")
        self.assertGreaterEqual(r["rsi"], 0)
        self.assertLessEqual(r["rsi"], 100)

    def test_falling_series_rsi_low(self):
        """下降趨勢 → RSI 偏低(<30，落 OVERSOLD)。"""
        df = make_df(falling_with_bounces())
        r = calc_rsi(df)
        self.assertIsNotNone(r["rsi"])
        self.assertLess(r["rsi"], 30)
        self.assertEqual(r["signal"], "OVERSOLD")

    def test_insufficient_data(self):
        """資料不足(< period+1) → 回 None 與 INSUFFICIENT_DATA，不丟例外。"""
        df = make_df([100, 101, 102])
        r = calc_rsi(df, period=14)
        self.assertIsNone(r["rsi"])
        self.assertEqual(r["signal"], "INSUFFICIENT_DATA")


class TestMA(unittest.TestCase):
    def test_bullish_alignment(self):
        """單調上升 → 價格站上所有均線 → BULLISH。"""
        df = make_df(list(range(60, 160)))  # 100 根遞增
        r = calc_ma(df, periods=[5, 10, 20, 60])
        self.assertEqual(r["alignment"], "BULLISH")
        self.assertEqual(sorted(r["above_ma"]), [5, 10, 20, 60])
        self.assertEqual(r["below_ma"], [])

    def test_bearish_alignment(self):
        """單調下降 → 價格跌破所有均線 → BEARISH。"""
        df = make_df(list(range(160, 60, -1)))
        r = calc_ma(df, periods=[5, 10, 20, 60])
        self.assertEqual(r["alignment"], "BEARISH")

    def test_ma_value_equals_mean(self):
        """MA 值須等於對應窗口收盤均值(以最後 5 根驗算)。"""
        closes = [10, 11, 12, 13, 14, 15]
        df = make_df(closes)
        r = calc_ma(df, periods=[5])
        expected = sum(closes[-5:]) / 5      # (11+12+13+14+15)/5 = 13
        self.assertAlmostEqual(r["ma"][5], expected, places=6)


class TestSuperTrend(unittest.TestCase):
    def test_uptrend_direction_up(self):
        df = make_df(list(range(80, 140)))   # 明確上升
        r = calc_supertrend(df)
        self.assertEqual(r["direction"], "UP")
        self.assertIsNotNone(r["supertrend"])
        self.assertGreater(r["atr"], 0)

    def test_downtrend_direction_down(self):
        df = make_df(list(range(140, 80, -1)))
        r = calc_supertrend(df)
        self.assertEqual(r["direction"], "DOWN")

    def test_supertrend_below_price_in_uptrend(self):
        """上升趨勢時 SuperTrend 線(=下軌)應在最後收盤價之下。"""
        closes = list(range(80, 140))
        df = make_df(closes)
        r = calc_supertrend(df)
        self.assertLess(r["supertrend"], closes[-1])

    def test_insufficient_data(self):
        df = make_df([100, 101, 102])
        r = calc_supertrend(df, period=10)
        self.assertIsNone(r["direction"])


class TestMACD(unittest.TestCase):
    def test_uptrend_bullish(self):
        df = make_df(rising_with_pullbacks(n=60))
        r = calc_macd(df)
        self.assertEqual(r["trend"], "BULLISH")
        self.assertGreater(r["macd"], r["signal_line"])

    def test_histogram_consistency(self):
        """histogram 必須等於 macd - signal_line(四捨五入到 6 位)。"""
        df = make_df(rising_with_pullbacks(n=60))
        r = calc_macd(df)
        self.assertAlmostEqual(r["histogram"], round(r["macd"] - r["signal_line"], 6), places=6)
        self.assertIn(r["crossover"], ("BULLISH_CROSS", "BEARISH_CROSS", "NONE"))

    def test_insufficient_data(self):
        df = make_df(list(range(100, 120)))  # < slow+signal=35
        r = calc_macd(df)
        self.assertIsNone(r["macd"])
        self.assertEqual(r["trend"], "INSUFFICIENT_DATA")


class TestBollinger(unittest.TestCase):
    def test_band_ordering_and_percent_b_range(self):
        """上軌>中軌>下軌；%B 在合理範圍；中軌=近 period 收盤均值。"""
        closes = rising_with_pullbacks(n=40)
        df = make_df(closes)
        r = calc_bollinger(df, period=20, std=2.0)
        self.assertGreater(r["upper"], r["middle"])
        self.assertGreater(r["middle"], r["lower"])
        self.assertAlmostEqual(r["middle"], sum(closes[-20:]) / 20, places=4)
        self.assertGreaterEqual(r["percent_b"], -0.5)  # 允許略破軌

    def test_constant_series_midband(self):
        """常數序列 → 帶寬 0、%B 取預設 0.5(除零保護分支)。"""
        df = make_df([50.0] * 30)
        r = calc_bollinger(df, period=20, std=2.0)
        self.assertEqual(r["bandwidth"], 0.0)
        self.assertEqual(r["percent_b"], 0.5)

    def test_insufficient_data(self):
        df = make_df([100, 101, 102])
        r = calc_bollinger(df, period=20)
        self.assertIsNone(r["upper"])
        self.assertEqual(r["position"], "INSUFFICIENT_DATA")


if __name__ == "__main__":
    unittest.main()
