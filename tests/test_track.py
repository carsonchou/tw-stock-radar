# -*- coding: utf-8 -*-
"""
test_track.py — 訊號命中率回灌(data_hunter/track.py)出場評估與聚合。

驗證：
  evaluate 只用 entry_date 之後的 K(無 look-ahead)、停損/停利/time-stop/open 分類、
           R 倍數 = pnl/|entry-stop|、ret_pct、同根停損優先
  aggregate open 不計入勝率分母、win_rate/long/short 分流
  record_signals 去重(同 entry_date+code+side 不重複)
注意：全程 monkeypatch scan._read_cache(合成 OHLC)，且不呼叫 track.update()，
      不觸碰正式 signals_book.json / 不連網 / 不下單。
"""
import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import scan   # noqa: E402
import track  # noqa: E402


def _cache_df(bars: dict) -> pd.DataFrame:
    """bars: {date_str: (high, low, close)} → 大寫欄位 + DatetimeIndex 的快取 DF。"""
    idx = [pd.Timestamp(d) for d in bars]
    highs = [v[0] for v in bars.values()]
    lows = [v[1] for v in bars.values()]
    closes = [v[2] for v in bars.values()]
    opens = closes
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes,
         "Volume": [1000] * len(idx)},
        index=pd.DatetimeIndex(idx),
    )


# 每個 code 對應一份合成快取
_CACHES = {
    # 做多停損：入場後首根跌破 95；入場前一根(03-07)刻意放天量觸價，須被忽略(no look-ahead)
    "LOSS": _cache_df({
        "2025-03-07": (999, 1, 500),      # 入場前，若被誤用會誤判 → 必須忽略
        "2025-03-11": (101, 94, 96),      # low 94 <= stop 95 → 停損
        "2025-03-12": (120, 118, 119),
    }),
    # 做多停利：先一根不觸發，隔根 high>=110
    "WIN": _cache_df({
        "2025-03-11": (105, 98, 103),
        "2025-03-12": (112, 104, 111),    # high 112 >= tp1 110 → 停利
    }),
    # 同根同時碰停損與停利 → 保守取停損(loss)
    "STOPWIN": _cache_df({
        "2025-03-11": (111, 94, 100),     # low94<=95 且 high111>=110 → 取停損
    }),
    # 評估期未滿且從未觸發 → open，以最後一根收盤標未實現
    "OPEN": _cache_df({
        "2025-03-11": (104, 98, 101),
        "2025-03-12": (105, 99, 103),     # 最後收盤 103 → +3%
    }),
}


def _fake_read_cache(code):
    return _CACHES.get(code)


class TestEvaluate(unittest.TestCase):
    def setUp(self):
        self._orig = scan._read_cache
        scan._read_cache = _fake_read_cache

    def tearDown(self):
        scan._read_cache = self._orig

    def _trade(self, code, **kw):
        base = {"code": code, "name": code, "side": "long",
                "entry_date": "2025-03-10", "entry": 100.0,
                "stop": 95.0, "tp1": 110.0, "tp2": 130.0, "status": "open"}
        base.update(kw)
        return base

    def test_loss_and_no_lookahead(self):
        t = track.evaluate(self._trade("LOSS"))
        self.assertEqual(t["result"], "loss")
        self.assertEqual(t["exit_reason"], "stop")
        self.assertEqual(t["exit"], 95.0)
        self.assertEqual(t["exit_date"], "2025-03-11")   # 用入場後首根，非入場前的 03-07
        self.assertAlmostEqual(t["r"], -1.0, places=2)   # (95-100)/5
        self.assertAlmostEqual(t["ret_pct"], -5.0, places=2)

    def test_win_tp1(self):
        t = track.evaluate(self._trade("WIN"))
        self.assertEqual(t["result"], "win")
        self.assertEqual(t["exit_reason"], "tp1")
        self.assertEqual(t["exit"], 110.0)
        self.assertAlmostEqual(t["r"], 2.0, places=2)    # (110-100)/5
        self.assertAlmostEqual(t["ret_pct"], 10.0, places=2)

    def test_stop_precedence_same_bar(self):
        t = track.evaluate(self._trade("STOPWIN"))
        self.assertEqual(t["result"], "loss")            # 同根保守取停損
        self.assertEqual(t["exit_reason"], "stop")

    def test_open_unrealized(self):
        t = track.evaluate(self._trade("OPEN"))
        self.assertEqual(t["status"], "open")
        self.assertEqual(t["exit_reason"], "open")
        self.assertAlmostEqual(t["ret_pct"], 3.0, places=2)   # 最後收盤 103

    def test_no_levels_guard(self):
        """缺 stop/tp1 → open/no_levels，不丟例外。"""
        t = track.evaluate(self._trade("WIN", stop=None))
        self.assertEqual(t["exit_reason"], "no_levels")
        self.assertEqual(t["status"], "open")


class TestAggregate(unittest.TestCase):
    def _t(self, side, result, status, r, ret, ed="2025-03-10"):
        return {"code": "X", "name": "X", "side": side, "result": result,
                "status": status, "r": r, "ret_pct": ret, "entry": 100.0,
                "entry_date": ed, "exit_date": ed, "exit": 100.0, "exit_reason": "tp1"}

    def test_win_rate_excludes_open(self):
        evaluated = [
            self._t("long", "win", "closed", 2.0, 10.0),
            self._t("long", "loss", "closed", -1.0, -5.0),
            self._t("short", "win", "closed", 1.5, 6.0),
            self._t("long", "win", "open", 0.5, 2.0),     # open：不計勝率分母
        ]
        agg = track.aggregate(evaluated)
        self.assertEqual(agg["n_closed"], 3)
        self.assertEqual(agg["n_open"], 1)
        self.assertAlmostEqual(agg["win_rate"], round(2 / 3, 3), places=3)
        self.assertAlmostEqual(agg["long_win_rate"], 0.5, places=3)   # 1勝1敗
        self.assertAlmostEqual(agg["short_win_rate"], 1.0, places=3)  # 1勝

    def test_empty(self):
        agg = track.aggregate([])
        self.assertEqual(agg["n_closed"], 0)
        self.assertEqual(agg["win_rate"], 0.0)


class TestRecordSignals(unittest.TestCase):
    def _state(self):
        return {
            "ok": True, "confirmed": True, "date": "2025-03-10",
            "signals": {
                "long": [{"code": "2330", "name": "台積電", "side": "long",
                          "price": 100.0, "stop": 95.0, "tp1": 110.0, "tp2": 130.0,
                          "confirmed": True}],
                "short": [{"code": "2317", "name": "鴻海", "side": "short",
                           "price": 50.0, "stop": 53.0, "tp1": 45.0, "tp2": 35.0,
                           "confirmed": True}],
            },
        }

    def test_dedup(self):
        book = []
        added1 = track.record_signals(self._state(), book)
        self.assertEqual(added1, 2)
        self.assertEqual(len(book), 2)
        # 同一天同訊號再記 → 0 新增(去重)
        added2 = track.record_signals(self._state(), book)
        self.assertEqual(added2, 0)
        self.assertEqual(len(book), 2)

    def test_unconfirmed_state_skipped(self):
        st = self._state()
        st["confirmed"] = False       # 盤中候選不記
        book = []
        self.assertEqual(track.record_signals(st, book), 0)


if __name__ == "__main__":
    unittest.main()
