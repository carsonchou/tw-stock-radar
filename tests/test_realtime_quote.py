# -*- coding: utf-8 -*-
"""
test_realtime_quote.py — 即時五檔解析(realtime_quote)純函式測試(不連網)。
用證交所 MIS getStockInfo 的假 msg 驗證五檔拆解、漲跌計算、缺值降級、上市櫃選取。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import realtime_quote as rq  # noqa: E402


# 仿 MIS msgArray 一筆(台積電，含五檔 a/b 價、f/g 量)
FAKE_MSG = {
    "c": "2330", "n": "台積電", "z": "2415.0000", "y": "2465.0000",
    "o": "2450.0000", "h": "2460.0000", "l": "2410.0000",
    "u": "2710.0000", "w": "2220.0000", "v": "12733", "tv": "120",
    "a": "2430.0000_2435.0000_2440.0000_2445.0000_2450.0000_",
    "b": "2425.0000_2420.0000_2415.0000_2410.0000_2405.0000_",
    "f": "204_663_503_562_493_", "g": "704_1358_1394_1199_573_",
    "tlong": "1751508835000",
}


class TestLevels(unittest.TestCase):
    def test_levels_pair_price_volume(self):
        lv = rq._levels(FAKE_MSG["a"], FAKE_MSG["f"])
        self.assertEqual(len(lv), 5)
        self.assertEqual(lv[0], {"price": 2430.0, "vol": 204})
        self.assertEqual(lv[4], {"price": 2450.0, "vol": 493})

    def test_levels_skip_empty(self):
        self.assertEqual(rq._levels("", ""), [])
        self.assertEqual(rq._levels("100.0_", "5_"), [{"price": 100.0, "vol": 5}])

    def test_num_handles_dashes(self):
        self.assertIsNone(rq._num("-"))
        self.assertIsNone(rq._num(""))
        self.assertEqual(rq._num("2415.00"), 2415.0)


class TestParse(unittest.TestCase):
    def test_parse_full(self):
        q = rq._parse(FAKE_MSG)
        self.assertEqual(q["code"], "2330")
        self.assertEqual(q["price"], 2415.0)
        self.assertEqual(q["prev_close"], 2465.0)
        self.assertEqual(q["chg"], -50.0)               # 2415 - 2465
        self.assertAlmostEqual(q["chg_pct"], -2.03, places=1)
        self.assertEqual(len(q["ask"]), 5)
        self.assertEqual(len(q["bid"]), 5)
        self.assertTrue(q["traded"])
        self.assertEqual(q["volume"], 12733)

    def test_parse_no_trade_falls_back_to_open_or_prevclose(self):
        # 成交價 '-'(盤前無成交) → 用開盤價當現價、漲跌以昨收算
        m = dict(FAKE_MSG); m["z"] = "-"
        q = rq._parse(m)
        self.assertFalse(q["traded"])
        self.assertEqual(q["price"], 2450.0)            # 退用開盤
        self.assertEqual(q["chg"], -15.0)               # 2450 - 2465

    def test_parse_missing_prevclose_no_chg(self):
        m = dict(FAKE_MSG); m["y"] = "-"
        q = rq._parse(m)
        self.assertIsNone(q["chg"])
        self.assertIsNone(q["chg_pct"])


if __name__ == "__main__":
    unittest.main()
