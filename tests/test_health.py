# -*- coding: utf-8 -*-
"""
test_health.py — 個股健診引擎(data_hunter/health.py)純函式測試(不連網)。

驗證：
  連續正規化 _squash/_logistic 邊界與單調性。
  compute_health 四面向加權、缺資料面向退出加權(信心度反映)、等級 A–E 分帶。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import health  # noqa: E402


class TestNormalizers(unittest.TestCase):
    def test_squash_bounds_and_monotonic(self):
        self.assertEqual(health._squash(0, 10, 20), 0.0)      # 低於下界→0
        self.assertEqual(health._squash(30, 10, 20), 1.0)     # 高於上界→1
        self.assertAlmostEqual(health._squash(15, 10, 20), 0.5)
        self.assertIsNone(health._squash(None, 10, 20))
        # 單調遞增
        self.assertLess(health._squash(12, 10, 20), health._squash(18, 10, 20))

    def test_logistic_midpoint_and_range(self):
        self.assertAlmostEqual(health._logistic(5, 5, 1), 0.5)   # mid → 0.5
        self.assertGreater(health._logistic(50, 5, 1), 0.99)     # 遠高 → ~1
        self.assertLess(health._logistic(-50, 5, 1), 0.01)       # 遠低 → ~0
        self.assertIsNone(health._logistic(None, 5, 1))

    def test_avg_ignores_none(self):
        # 加權平均忽略 None 項；全 None → None
        self.assertAlmostEqual(health._avg([(1, 0.4), (1, 0.6)]), 0.5)
        self.assertAlmostEqual(health._avg([(2, 1.0), (1, None)]), 1.0)
        self.assertIsNone(health._avg([(1, None), (2, None)]))


def _strong_tech():
    return {"st": "UP", "adx": 40, "rsi": 65, "macd_trend": "up",
            "above20": True, "above60": True, "percent_b": 0.8}


class TestComputeHealth(unittest.TestCase):
    def test_strong_all_pillars_high_grade(self):
        d = dict(_strong_tech())
        d.update({"consec_buy_days": 7, "instinv_net": 5000, "trust_consec_days": 5,
                  "margin_chg": -1000, "retail_exit": True,
                  "eps_ttm": 20, "eps_yoy": 40, "rev_yoy": 30, "gross_margin": 40,
                  "pe": 10, "pb": 1.0, "dividend_yield": 5})
        h = health.compute_health(d)
        self.assertGreaterEqual(h["overall"], 80)
        self.assertEqual(h["grade"], "A")
        self.assertEqual(h["confidence"], 1.0)          # 四面向皆有資料
        self.assertTrue(all(p["has_data"] for p in h["pillars"].values()))

    def test_missing_fundamentals_lowers_confidence_not_score_to_zero(self):
        d = dict(_strong_tech())
        d.update({"consec_buy_days": 5, "instinv_net": 3000})
        # 無基本面/估值資料
        h = health.compute_health(d)
        self.assertLess(h["confidence"], 1.0)           # 少了面向 → 信心度下降
        self.assertFalse(h["pillars"]["基本面"]["has_data"])
        self.assertFalse(h["pillars"]["估值"]["has_data"])
        # 缺資料面向不把總分靜默拉低到 0：技術+籌碼強 → 總分仍高
        self.assertGreater(h["overall"], 60)

    def test_weak_stock_low_grade(self):
        d = {"st": "DOWN", "adx": 12, "rsi": 35, "macd_trend": "down",
             "above20": False, "above60": False, "percent_b": 0.1,
             "consec_buy_days": 0, "instinv_net": -5000,
             "eps_ttm": -2, "eps_yoy": -30, "rev_yoy": -20, "gross_margin": 6,
             "pe": 60, "pb": 8, "dividend_yield": 0}
        h = health.compute_health(d)
        self.assertLess(h["overall"], 40)
        self.assertIn(h["grade"], ("D", "E"))

    def test_roe_derived_from_eps_pb_price(self):
        # ROE ≈ EPS_ttm × PB / price：74.39×11.03/2465 ≈ 33.3%
        self.assertAlmostEqual(health._roe_est(
            {"eps_ttm": 74.39, "pb": 11.03, "price": 2465}), 33.3, delta=0.5)
        self.assertIsNone(health._roe_est({"eps_ttm": 5, "pb": None, "price": 100}))
        self.assertIsNone(health._roe_est({"eps_ttm": None, "pb": 2, "price": 100}))

    def test_all_missing_returns_none_overall(self):
        h = health.compute_health({})
        # 技術面全缺 → 各面向無資料 → overall None、grade —
        self.assertIsNone(h["overall"])
        self.assertEqual(h["grade"], "—")
        self.assertEqual(h["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
