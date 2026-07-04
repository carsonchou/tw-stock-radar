# -*- coding: utf-8 -*-
"""
test_json_safe.py — state.json 合法性守門(scan._json_safe)。
NaN/Inf 是非法 JSON，會讓前端 JSON.parse 整包失敗、看板全白 → 必須清成 null。
"""
import json
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import scan  # noqa: E402


class TestJsonSafe(unittest.TestCase):
    def test_nan_inf_become_none(self):
        obj = {"a": float("nan"), "b": float("inf"), "c": -float("inf"),
               "d": 1.5, "e": [float("nan"), 2, {"f": float("nan")}]}
        safe = scan._json_safe(obj)
        self.assertIsNone(safe["a"]); self.assertIsNone(safe["b"]); self.assertIsNone(safe["c"])
        self.assertEqual(safe["d"], 1.5)
        self.assertIsNone(safe["e"][0]); self.assertEqual(safe["e"][1], 2)
        self.assertIsNone(safe["e"][2]["f"])

    def test_output_is_strict_valid_json(self):
        # 含 NaN 的結構經 _json_safe 後，json.dumps(allow_nan=False) 不得拋、且能被嚴格 parse
        s = json.dumps(scan._json_safe({"x": float("nan"), "y": [1, float("inf")]}), allow_nan=False)
        self.assertNotIn("NaN", s)
        json.loads(s)   # 嚴格 parse(等同前端 JSON.parse)不得失敗

    def test_normal_values_untouched(self):
        obj = {"s": "台積電", "n": 42, "f": 3.14, "l": [1, 2, 3], "b": True, "z": None}
        self.assertEqual(scan._json_safe(obj), obj)


if __name__ == "__main__":
    unittest.main()
