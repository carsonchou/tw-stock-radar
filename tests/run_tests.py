# -*- coding: utf-8 -*-
"""
run_tests.py — 一鍵跑數據獵手單元測試(標準庫 unittest，無 pytest 依賴)。

用法：
  /d/ClawWork/.venv/Scripts/python.exe run_tests.py
或(等價)：
  python -m unittest discover -s tests -p "test_*.py"
"""
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(HERE), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
