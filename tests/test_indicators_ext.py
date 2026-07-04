# -*- coding: utf-8 -*-
"""
test_indicators_ext.py — 擴充技術指標(quant-service/indicators.py 新增 OBV/DMI/威廉/CCI/寶塔線)。
用合成 K 線(不連網)驗證方向性與邊界。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402  (掛 sys.path)
import indicators as ind  # noqa: E402
from _util import make_df, rising_with_pullbacks, falling_with_bounces  # noqa: E402


class TestExtendedIndicators(unittest.TestCase):
    def test_obv_trend_up_on_rising(self):
        df = make_df(rising_with_pullbacks(50), vol=[1000.0] * 50)
        self.assertEqual(ind.calc_obv(df)["trend"], "up")

    def test_dmi_plus_gt_minus_on_uptrend(self):
        df = make_df(rising_with_pullbacks(60))
        r = ind.calc_dmi(df)
        self.assertIsNotNone(r["plus_di"])
        self.assertEqual(r["signal"], "多")
        self.assertGreaterEqual(r["plus_di"], r["minus_di"])

    def test_dmi_minus_gt_plus_on_downtrend(self):
        df = make_df(falling_with_bounces(60))
        r = ind.calc_dmi(df)
        self.assertEqual(r["signal"], "空")

    def test_williams_bounds_and_oversold(self):
        df = make_df(falling_with_bounces(40))
        r = ind.calc_williams_r(df)
        self.assertTrue(-100.0 <= r["williams_r"] <= 0.0)   # %R 恆在 [-100,0]
        self.assertIn(r["zone"], ("超買", "超賣", "中性"))

    def test_cci_positive_on_uptrend(self):
        df = make_df(rising_with_pullbacks(50))
        r = ind.calc_cci(df)
        self.assertIsNotNone(r["cci"])
        self.assertGreater(r["cci"], 0)                      # 上升趨勢 CCI 為正

    def test_tower_red_on_uptrend_black_on_downtrend(self):
        self.assertEqual(ind.calc_tower(make_df(rising_with_pullbacks(40)))["tower"], "紅")
        self.assertEqual(ind.calc_tower(make_df(falling_with_bounces(40)))["tower"], "黑")

    def test_resample_weekly_monthly(self):
        df = make_df(rising_with_pullbacks(120))              # ~120 個工作日 ≈ 24 週 ≈ 6 月
        wk = ind.resample_ohlc(df, "W", 40)
        mo = ind.resample_ohlc(df, "M", 40)
        self.assertGreater(len(wk), 10)                       # 週K 應多於月K
        self.assertGreater(len(mo), 3)
        self.assertLess(len(mo), len(wk))
        self.assertEqual(len(wk[0]), 4)                       # 每根 [o,h,l,c]

    def test_insufficient_data_returns_none(self):
        df = make_df([100.0, 101.0])                          # 資料太少
        self.assertIsNone(ind.calc_dmi(df)["plus_di"])
        self.assertIsNone(ind.calc_williams_r(df)["williams_r"])


if __name__ == "__main__":
    unittest.main()
