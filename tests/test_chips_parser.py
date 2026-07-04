# -*- coding: utf-8 -*-
"""
test_chips_parser.py — 三大法人籌碼 parser(data_hunter/chips.py)。

用官方 T86 / TPEX 假 JSON 餵 parser(不連網，monkeypatch _get_json)，驗證：
  外資淨買 = 外陸資(不含自營) + 外資自營 兩欄相加(= 含外資自營)
  ÷1000 成張、去逗號、'--'→0、ETF(00開頭)濾除
  load_chips 連買天數以『實際有資料日』計、單日缺資料中斷連買、近 N 日淨買加總
"""
import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import chips  # noqa: E402


# ── 官方 T86(selectType=ALL) 19 欄格式的假回傳 ──────────────────────────────
_T86_FIELDS = [
    "證券代號", "證券名稱",
    "外陸資買進股數(不含外資自營商)", "外陸資賣出股數(不含外資自營商)",
    "外陸資買賣超股數(不含外資自營商)",              # idx 4  = foreign_excl
    "外資自營商買進股數", "外資自營商賣出股數",
    "外資自營商買賣超股數",                          # idx 7  = foreign_dealer
    "投信買進股數", "投信賣出股數",
    "投信買賣超股數",                                # idx 10 = trust
    "自營商買賣超股數",
    "自營商買進股數(自行買賣)", "自營商賣出股數(自行買賣)", "自營商買賣超股數(自行買賣)",
    "自營商買進股數(避險)", "自營商賣出股數(避險)", "自營商買賣超股數(避險)",
    "三大法人買賣超股數",                            # idx 18 = total（放最後，len=20）
]


def _t86_row(code, name, f_excl, f_deal, trust, total):
    """依 _T86_FIELDS 佈局造一列(僅關鍵欄有值，其餘填 '0')。"""
    row = [code, name] + ["0"] * (len(_T86_FIELDS) - 2)
    row[4], row[7], row[10], row[18] = f_excl, f_deal, trust, total
    return row


def _fake_t86_json():
    return {
        "stat": "OK",
        "fields": _T86_FIELDS,
        "data": [
            # 2330：外資不含自營 1,200,000 + 外資自營 50,000 = 1,250,000 股 → 1250 張
            _t86_row("2330", "台積電", "1,200,000", "50,000", "300,000", "1,550,000"),
            # 2317：含負值與逗號
            _t86_row("2317", "鴻海", "-800,000", "0", "-100,000", "-900,000"),
            # 0050：ETF 應被濾除
            _t86_row("0050", "元大台灣50", "500,000", "0", "0", "500,000"),
            # '--' 視為 0
            _t86_row("2603", "長榮", "--", "--", "250,000", "250,000"),
            # 欄位不足的雜列(小計/說明) → 應跳過
            ["說明", "資料日期"],
        ],
    }


class TestT86Parser(unittest.TestCase):
    def setUp(self):
        self._orig = chips._get_json
        chips._get_json = lambda url, timeout=25: _fake_t86_json()

    def tearDown(self):
        chips._get_json = self._orig

    def test_foreign_is_excl_plus_dealer_in_lots(self):
        out = chips.fetch_t86(date(2026, 6, 30))
        self.assertIn("2330", out)
        # (1,200,000 + 50,000)/1000 = 1250 張
        self.assertEqual(out["2330"]["foreign_net"], 1250)
        self.assertEqual(out["2330"]["trust_net"], 300)     # 300,000/1000
        self.assertEqual(out["2330"]["instinv_net"], 1550)  # 官方合計欄

    def test_negative_and_comma(self):
        out = chips.fetch_t86(date(2026, 6, 30))
        self.assertEqual(out["2317"]["foreign_net"], -800)   # -800,000/1000
        self.assertEqual(out["2317"]["trust_net"], -100)

    def test_etf_filtered(self):
        out = chips.fetch_t86(date(2026, 6, 30))
        self.assertNotIn("0050", out)                        # 00 開頭 ETF 濾掉

    def test_dashes_as_zero(self):
        out = chips.fetch_t86(date(2026, 6, 30))
        self.assertEqual(out["2603"]["foreign_net"], 0)      # '--' → 0
        self.assertEqual(out["2603"]["trust_net"], 250)

    def test_bad_json_returns_none(self):
        chips._get_json = lambda url, timeout=25: {"stat": "查詢無資料"}
        self.assertIsNone(chips.fetch_t86(date(2026, 6, 30)))


# ── 官方 TPEX 避險版(24 欄)假回傳 ──────────────────────────────────────────
def _tpex_row(code, name, f_excl, f_deal, trust, total):
    row = [code, name] + ["0"] * 22        # 共 24 欄
    row[chips._TPEX_IDX["foreign_excl"]] = f_excl
    row[chips._TPEX_IDX["foreign_dealer"]] = f_deal
    row[chips._TPEX_IDX["trust"]] = trust
    row[chips._TPEX_IDX["total"]] = total
    return row


def _fake_tpex_json():
    return {
        "tables": [{
            "fields": [f"c{i}" for i in range(24)],   # 只在乎欄數 >=24
            "data": [
                _tpex_row("5483", "中美晶", "600,000", "20,000", "100,000", "720,000"),
                _tpex_row("00679B", "元大美債", "1,000", "0", "0", "1,000"),  # ETF 濾除
            ],
        }],
    }


class TestTpexParser(unittest.TestCase):
    def setUp(self):
        self._orig = chips._get_json
        chips._get_json = lambda url, timeout=25: _fake_tpex_json()

    def tearDown(self):
        chips._get_json = self._orig

    def test_tpex_foreign_and_lots(self):
        out = chips.fetch_tpex(date(2026, 6, 30))
        self.assertIn("5483", out)
        self.assertEqual(out["5483"]["foreign_net"], 620)   # (600,000+20,000)/1000
        self.assertEqual(out["5483"]["trust_net"], 100)
        self.assertEqual(out["5483"]["instinv_net"], 720)
        self.assertNotIn("00679B", out)

    def test_wrong_column_count_returns_none(self):
        chips._get_json = lambda url, timeout=25: {
            "tables": [{"fields": [f"c{i}" for i in range(10)], "data": [["5483"] + ["0"] * 9]}]}
        self.assertIsNone(chips.fetch_tpex(date(2026, 6, 30)))


# ── load_chips 連買天數 / 近 N 日加總(以『實際有資料日』計) ──────────────────
class TestLoadChipsAggregation(unittest.TestCase):
    def setUp(self):
        # 三個交易日(新→舊)；純合成、不觸檔案系統
        self.d1, self.d2, self.d3 = date(2026, 6, 30), date(2026, 6, 27), date(2026, 6, 26)
        self._orig_rtd = chips.recent_trading_days
        self._orig_ldc = chips.load_day_cache
        chips.recent_trading_days = lambda days=5, end=None, offline=False: [self.d1, self.d2, self.d3]

        # 2330：d1/d2 外資+投信皆>0(連買2)，d3 轉負(中斷)
        # 2317：d1 有、d2 無此檔(連買中斷)、d3 有
        day = {
            self.d1: {
                "2330": {"foreign_net": 1000, "trust_net": 200, "instinv_net": 1300},
                "2317": {"foreign_net": 500, "trust_net": 100, "instinv_net": 700},
            },
            self.d2: {
                "2330": {"foreign_net": 800, "trust_net": 100, "instinv_net": 950},
                # 2317 當日無資料
            },
            self.d3: {
                "2330": {"foreign_net": -300, "trust_net": -50, "instinv_net": -400},
                "2317": {"foreign_net": 200, "trust_net": 0, "instinv_net": 200},
            },
        }
        chips.load_day_cache = lambda d: day.get(d)

    def tearDown(self):
        chips.recent_trading_days = self._orig_rtd
        chips.load_day_cache = self._orig_ldc

    def test_consec_buy_days(self):
        out = chips.load_chips(["2330"], days=5, offline=True)
        self.assertEqual(out["2330"]["consec_buy_days"], 2)   # d1,d2 連買後 d3 中斷
        # net_sum_n = (1000+200)+(800+100)+(-300-50) = 1750
        self.assertEqual(out["2330"]["net_sum_n"], 1750)
        self.assertEqual(out["2330"]["foreign_net"], 1000)    # 最近交易日值
        self.assertEqual(out["2330"]["date"], self.d1.isoformat())

    def test_missing_day_breaks_streak(self):
        out = chips.load_chips(["2317"], days=5, offline=True)
        # d1 有(連買1)、d2 無此檔 → 中斷，故 consec=1
        self.assertEqual(out["2317"]["consec_buy_days"], 1)


if __name__ == "__main__":
    unittest.main()
