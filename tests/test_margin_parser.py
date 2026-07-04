# -*- coding: utf-8 -*-
"""
test_margin_parser.py — 融資融券 / 當沖 parser(data_hunter/margin.py)。

不連網(monkeypatch)。驗證：
  MI_MARGN 明細表欄名定位(今日/前日餘額 出現順序) + 融資餘額/前日餘額/融券餘額
  TWTB4U(exchangeReport 版) 當沖成交股數欄偵測
  fetch_day 收尾：單位自動偵測(股→÷1000)、券資比=融券/融資×100、融資增減、
             當沖整段缺→None(非 0)、抓到但個股無當沖列→0(真 0)、ETF 濾除
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import margin  # noqa: E402


# ── MI_MARGN 個股明細假回傳(14 欄) ─────────────────────────────────────────
_MI_FIELDS = [
    "股票代號", "股票名稱",
    "融資買進", "融資賣出", "融資現金償還", "融資前日餘額", "融資今日餘額", "融資限額",
    "融券買進", "融券賣出", "融券現券償還", "融券前日餘額", "融券今日餘額", "融券限額",
]


def _mi_row(code, m_today, m_prev, s_today):
    row = [code, code + "名"] + ["0"] * 12
    row[6], row[5], row[12] = m_today, m_prev, s_today   # 融資今餘/前餘、融券今餘
    return row


def _fake_mi_json():
    return {
        "stat": "OK",
        "tables": [{
            "fields": _MI_FIELDS,
            "data": [
                # 單位=股(需 ÷1000)：融資今 5,000,000 前 4,000,000 融券 1,000,000
                _mi_row("2330", "5,000,000", "4,000,000", "1,000,000"),
                _mi_row("2317", "3,000,000", "3,200,000", "600,000"),
                _mi_row("2454", "2,000,000", "1,900,000", "0"),
                _mi_row("2603", "8,000,000", "7,000,000", "4,000,000"),
                _mi_row("2609", "6,000,000", "6,000,000", "300,000"),
                _mi_row("0050", "9,000,000", "9,000,000", "0"),   # ETF → 應濾除
            ],
        }],
    }


class TestMiMargnParser(unittest.TestCase):
    def setUp(self):
        self._orig = margin._get_json
        margin._get_json = lambda url, timeout=25: _fake_mi_json()

    def tearDown(self):
        margin._get_json = self._orig

    def test_raw_parse_and_etf_filter(self):
        out = margin.fetch_margin_twse(date(2026, 6, 30))
        self.assertEqual(out["2330"]["margin_balance"], 5_000_000)   # 原始股數，未除
        self.assertEqual(out["2330"]["margin_prev"], 4_000_000)
        self.assertEqual(out["2330"]["short_balance"], 1_000_000)
        self.assertNotIn("0050", out)                                # ETF 濾除

    def test_col_index_by_name_order(self):
        """欄名定位：今日餘額兩次(融資在前融券在後)、前日餘額(融資)。"""
        ci = margin._margin_col_index(_MI_FIELDS)
        self.assertEqual(ci, {"m_today": 6, "m_prev": 5, "s_today": 12})

    def test_fallback_index_when_names_missing(self):
        """欄名缺(定位失敗) → 回 None，fetch 端改用 _MI_FALLBACK 固定索引。"""
        self.assertIsNone(margin._margin_col_index(["a"] * 14))
        self.assertEqual(margin._MI_FALLBACK, {"m_today": 6, "m_prev": 5, "s_today": 12})


# ── 當沖 TWTB4U(exchangeReport tables 版)假回傳 ────────────────────────────
class TestDaytradeParser(unittest.TestCase):
    def setUp(self):
        self._orig = margin._get_json

    def tearDown(self):
        margin._get_json = self._orig

    def test_daytrade_shares_column_detected(self):
        fields = ["證券代號", "證券名稱", "當日沖銷交易成交股數",
                  "當日沖銷交易買進成交金額", "當日沖銷交易賣出成交金額"]
        margin._get_json = lambda url, timeout=25: {
            "tables": [
                {"fields": ["x"], "data": []},                        # 空表(不該被選)
                {"fields": fields, "data": [
                    ["2330", "台積電", "3,000,000", "999", "999"],
                    ["0050", "元大台灣50", "1,000", "1", "1"],         # ETF 濾除
                ]},
            ]}
        out = margin.fetch_daytrade_twse(date(2026, 6, 30))
        self.assertEqual(out["2330"], 3_000_000)     # 回傳原始股數(不除)
        self.assertNotIn("0050", out)


# ── fetch_day 收尾：單位偵測 / 券資比 / 當沖 None vs 0 ──────────────────────
class TestFetchDayFinalize(unittest.TestCase):
    def setUp(self):
        self._orig = {
            "twse": margin.fetch_margin_twse,
            "tpex": margin.fetch_margin_tpex,
            "dt": margin.fetch_daytrade_twse,
            "thr": margin.THROTTLE_SEC,
        }
        margin.THROTTLE_SEC = 0                       # 免等節流
        margin.fetch_margin_tpex = lambda d: None
        # 原始『股』單位(median > 30萬 → ÷1000)
        margin.fetch_margin_twse = lambda d: {
            "2330": {"margin_balance": 5_000_000, "margin_prev": 4_000_000,
                     "short_balance": 1_000_000},
            "2317": {"margin_balance": 3_000_000, "margin_prev": 3_200_000,
                     "short_balance": 600_000},
            "9999": {"margin_balance": 0, "margin_prev": 0, "short_balance": 0},
        }

    def tearDown(self):
        margin.fetch_margin_twse = self._orig["twse"]
        margin.fetch_margin_tpex = self._orig["tpex"]
        margin.fetch_daytrade_twse = self._orig["dt"]
        margin.THROTTLE_SEC = self._orig["thr"]

    def test_unit_divide_and_ratio(self):
        margin.fetch_daytrade_twse = lambda d: None   # 當沖整段抓不到
        out = margin.fetch_day(date(2026, 6, 30))
        self.assertEqual(out["2330"]["margin_balance"], 5000)    # 5,000,000/1000
        self.assertEqual(out["2330"]["margin_chg"], 1000)        # 5000-4000
        self.assertEqual(out["2330"]["short_balance"], 1000)
        self.assertEqual(out["2330"]["short_margin_ratio"], 20.0)  # 1000/5000*100
        # 融資=0 → 券資比取 0.0(除零保護)
        self.assertEqual(out["9999"]["short_margin_ratio"], 0.0)
        # 當沖整段抓失敗 → None(非 0，避免誤顯示)
        self.assertIsNone(out["2330"]["day_trade_lots"])

    def test_daytrade_none_vs_zero(self):
        # 抓到當沖，但只有 2330 有列；2317 無列 → 0(真 0)
        margin.fetch_daytrade_twse = lambda d: {"2330": 3_000_000}
        out = margin.fetch_day(date(2026, 6, 30))
        self.assertEqual(out["2330"]["day_trade_lots"], 3000)    # 3,000,000/1000
        self.assertEqual(out["2317"]["day_trade_lots"], 0)       # 抓到但無列 → 真 0

    def test_detect_div_lots_already(self):
        """已是『張』單位(median <= 30萬)→ 不再除。"""
        margin.fetch_margin_twse = lambda d: {
            "2603": {"margin_balance": 8000, "margin_prev": 7000, "short_balance": 4000},
            "2609": {"margin_balance": 6000, "margin_prev": 6000, "short_balance": 300},
        }
        margin.fetch_daytrade_twse = lambda d: None
        out = margin.fetch_day(date(2026, 6, 30))
        self.assertEqual(out["2603"]["margin_balance"], 8000)    # 未被 ÷1000
        self.assertEqual(out["2603"]["short_margin_ratio"], 50.0)


class TestDetectDiv(unittest.TestCase):
    def test_shares_scale(self):
        big = {"a": {"margin_balance": 5_000_000}, "b": {"margin_balance": 4_000_000}}
        self.assertEqual(margin._detect_div(big), 1000)

    def test_lots_scale(self):
        small = {"a": {"margin_balance": 5000}, "b": {"margin_balance": 4000}}
        self.assertEqual(margin._detect_div(small), 1)

    def test_empty(self):
        self.assertEqual(margin._detect_div({}), 1)


if __name__ == "__main__":
    unittest.main()
