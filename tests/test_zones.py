# -*- coding: utf-8 -*-
"""
test_zones.py — 交易專區 setup/評分/手把手(zones)純函式測試(不連網、合成 stocks)。
驗證每風格 setup 命中規則、排序方向、流動性門檻、play 生成。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import zones  # noqa: E402


def _stk(**kw):
    base = {"code": "1234", "name": "測試", "industry": "測", "price": 100.0, "chg": 1.0,
            "turnover_60d": 5e8, "day_trade_pct": None, "amplitude": None, "relvol": 1.0,
            "recent_high20": 100.0, "ohlc": [[99, 101, 98, 100]] * 5,
            "score": 50, "firm": False, "signal": None, "consec_buy_days": 0,
            "above20": False, "above60": False, "st": "NEUTRAL", "rsi": 50,
            "stop": None, "tp1": None, "tp2": None, "hi60": 100, "lo60": 90,
            "pe": None, "pb": None, "dividend_yield": None, "eps_est": None,
            "roe_est": None, "rev_yoy": None}
    base.update(kw)
    return base


class TestDaytrade(unittest.TestCase):
    def test_baofang_breakout_setup(self):
        s = _stk(relvol=2.5, price=100, recent_high20=100, amplitude=5, day_trade_pct=40)
        out = zones._daytrade([s])
        self.assertEqual(len(out), 1)
        self.assertIn("爆量突破", out[0]["setups"])
        self.assertGreater(out[0]["zscore"], 60)

    def test_liquidity_filter_excludes_illiquid(self):
        s = _stk(turnover_60d=5e7, relvol=3, amplitude=8, day_trade_pct=50)  # < 1e8
        self.assertEqual(zones._daytrade([s]), [])

    def test_play_has_four_lines(self):
        s = _stk(relvol=2.5, recent_high20=100, amplitude=5, day_trade_pct=40)
        p = zones._daytrade([s])[0]["play"]
        for k in ("entry", "stop", "target", "note"):
            self.assertTrue(p[k])

    def test_sorted_desc(self):
        hi = _stk(code="A", day_trade_pct=50, amplitude=8, relvol=3, turnover_60d=2e9)
        lo = _stk(code="B", day_trade_pct=12, amplitude=2.5, relvol=1.2, turnover_60d=2e8)
        out = zones._daytrade([lo, hi])
        self.assertEqual(out[0]["code"], "A")


class TestSwing(unittest.TestCase):
    def test_setups(self):
        s = _stk(signal="long", firm=True, above20=True, st="UP",
                 price=100, recent_high20=100, consec_buy_days=5, score=80)
        tags = zones._swing([s])[0]["setups"]
        self.assertIn("翻多起漲", tags)
        self.assertIn("突破前高", tags)
        self.assertIn("法人連買強勢", tags)

    def test_stop_in_play(self):
        s = _stk(signal="long", firm=True, score=70, stop=95.0, tp1=110, tp2=120)
        p = zones._swing([s])[0]["play"]
        self.assertIn("95", p["stop"])

    def test_weak_excluded(self):
        s = _stk(score=20)   # 無 setup、zscore 低
        self.assertEqual(zones._swing([s]), [])


class TestLongterm(unittest.TestCase):
    def test_high_yield_setup(self):
        s = _stk(dividend_yield=6.0, eps_est=8.0, above60=True, pe=12, roe_est=20)
        self.assertIn("高殖利率存股", zones._longterm([s])[0]["setups"])

    def test_value_growth_setup(self):
        s = _stk(pe=15, rev_yoy=10, roe_est=15, above60=True, dividend_yield=3, eps_est=5)
        self.assertIn("價值成長", zones._longterm([s])[0]["setups"])

    def test_etf_setup(self):
        s = _stk(code="0056", dividend_yield=7, eps_est=2, above60=True, pe=10)
        self.assertIn("ETF專區", zones._longterm([s])[0]["setups"])


if __name__ == "__main__":
    unittest.main()
