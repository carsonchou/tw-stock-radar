# -*- coding: utf-8 -*-
"""
test_tdcc_parser.py — TDCC 集保戶數 parser(data_hunter/tdcc.py)。

驗證：
  _parse_json：小散戶 = 級 1+2+3 戶數合計、總戶數 = 級 16、缺級16 → None
  load_tdcc：兩個 encDate 週差值、週變化%、retail_exit/surge 旗標、前週缺 → None
全程純合成資料、不連網。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import tdcc  # noqa: E402


def _aa(rows):
    """rows: [(grade, count), ...] → 造 aaData(每列 [grade, max, count, shares, pct])。"""
    return {"aaData": [[str(g), "999", f"{c:,}", "0", "0.00"] for g, c in rows]}


class TestParseJson(unittest.TestCase):
    def test_small_is_grade_1_2_3(self):
        j = _aa([(1, 50_000), (2, 30_000), (3, 10_000),
                 (4, 5_000), (9, 2_000), (16, 200_000)])
        r = tdcc._parse_json(j)
        self.assertEqual(r["small_count"], 90_000)     # 50k+30k+10k
        self.assertEqual(r["total_count"], 200_000)    # 級16 合計

    def test_missing_grade16_returns_none(self):
        j = _aa([(1, 50_000), (2, 30_000), (3, 10_000)])   # 無級16
        self.assertIsNone(tdcc._parse_json(j))

    def test_empty_returns_none(self):
        self.assertIsNone(tdcc._parse_json({"aaData": []}))
        self.assertIsNone(tdcc._parse_json({}))


class TestLoadTdccWeekChange(unittest.TestCase):
    def setUp(self):
        self._orig_fad = tdcc._find_available_dates
        self._orig_ed = tdcc.ensure_date
        tdcc._find_available_dates = lambda n=2, offline=False: ["20260627", "20260620"]

        latest = {
            "2330": {"small_count": 90_000, "total_count": 500_000},   # 散戶↓
            "2317": {"small_count": 95_000, "total_count": 400_000},   # 散戶↑
            "2454": {"small_count": 40_000, "total_count": 100_000},   # 前週缺
        }
        prev = {
            "2330": {"small_count": 95_000, "total_count": 505_000},
            "2317": {"small_count": 90_000, "total_count": 398_000},
            # 2454 前週無資料
        }

        def _ensure(enc_date, codes, offline=False):
            src = latest if enc_date == "20260627" else prev
            return {c: src[c] for c in codes if c in src}

        tdcc.ensure_date = _ensure

    def tearDown(self):
        tdcc._find_available_dates = self._orig_fad
        tdcc.ensure_date = self._orig_ed

    def test_retail_exit(self):
        out = tdcc.load_tdcc(["2330"])
        r = out["2330"]
        self.assertEqual(r["small_chg"], -5_000)                  # 90k-95k
        self.assertAlmostEqual(r["small_chg_pct"], -5.26, places=2)  # -5000/95000
        self.assertTrue(r["retail_exit"])                         # <= -2%
        self.assertFalse(r["retail_surge"])
        self.assertEqual(r["enc_date"], "20260627")

    def test_retail_surge(self):
        out = tdcc.load_tdcc(["2317"])
        r = out["2317"]
        self.assertGreater(r["small_chg_pct"], 3.0)
        self.assertTrue(r["retail_surge"])
        self.assertFalse(r["retail_exit"])

    def test_missing_prev_week_none(self):
        out = tdcc.load_tdcc(["2454"])
        r = out["2454"]
        self.assertIsNone(r["small_count_prev"])
        self.assertIsNone(r["small_chg"])
        self.assertIsNone(r["small_chg_pct"])
        self.assertFalse(r["retail_exit"])                        # None → 不觸旗標
        self.assertFalse(r["retail_surge"])


if __name__ == "__main__":
    unittest.main()
