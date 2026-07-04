"""CLI entry point: tw-stock-radar"""
import os, sys
from pathlib import Path


def main():
    # When installed via pip, __file__ is in site-packages/tw_stock_radar/__main__.py
    # py-modules (app.py, scan.py, etc.) are installed to site-packages/ (one level up)
    pkg_dir = Path(__file__).resolve().parent.parent  # site-packages/
    os.chdir(str(pkg_dir))
    sys.path.insert(0, str(pkg_dir))
    import app
    app.main()


if __name__ == "__main__":
    main()
