# -*- coding: utf-8 -*-
"""
snapshot.py — 數據獵手看板一鍵 IG/YT 快照（用系統 Chrome headless，零相依）

對 dashboard.html?snapshot=1 截兩種尺寸：
  · IG 直式 1080×1350（4:5 直式，IG 貼文主流比例）
  · YT 橫式 1920×1080（16:9，影片封面/縮圖底圖）

快照模式（?snapshot=1）會自動切成精簡直式版面：隱藏互動/分頁/雜訊，
放大標題與溫度爐，並印上「量化阿森 Carson Quant」浮水印＋日期＋免責。

────────────────────────────────────────────────────────
用法（PowerShell / cmd）：
  # 1) 先在另一個視窗起一個看板伺服器（任一即可）：
  python server.py 8910
  #    或純標準庫：python -m http.server 8910

  # 2) 截圖（預設抓 http://127.0.0.1:8910）：
  python snapshot.py
  python snapshot.py --port 8910
  python snapshot.py --url http://127.0.0.1:8910 --mock   # 用 state.sample.json 測
  python snapshot.py --only ig        # 只截 IG 直式
  python snapshot.py --only yt        # 只截 YT 橫式
  python snapshot.py --out D:\path\to\folder

  # 不想自己開伺服器？加 --serve 讓本程式臨時起一個再截，截完關閉：
  python snapshot.py --serve

輸出：snapshots/ig_YYYYMMDD_HHMM.png、snapshots/yt_YYYYMMDD_HHMM.png
────────────────────────────────────────────────────────
找 Chrome：自動掃常見安裝路徑；可用環境變數 CHROME 或 --chrome 指定。
渲染等待：headless 會等 --virtual-time-budget 毫秒讓字體/動畫/抓 state 完成。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

# Windows 主控台預設 cp950，繁中/符號輸出會炸 → 強制 UTF-8
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = Path(__file__).resolve().parent

# 兩種輸出尺寸（寬,高）
SIZES = {
    "ig": (1080, 1350),   # IG 直式 4:5（貼文最大版面，比 9:16 更吃截圖）
    "yt": (1920, 1080),   # YT 橫式 16:9
}

CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",  # Edge 也吃同套 flag
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def find_chrome(explicit: str | None) -> str:
    cands = [explicit] if explicit else []
    cands += CHROME_CANDIDATES
    for c in cands:
        if c and Path(c).exists():
            return c
    # 退而求其次：PATH 上的 chrome
    for name in ("chrome", "chrome.exe", "msedge", "msedge.exe"):
        p = shutil.which(name)
        if p:
            return p
    sys.exit("[snapshot] 找不到 Chrome/Edge，請用 --chrome 指定 chrome.exe 路徑或設環境變數 CHROME。")


def start_local_server(port: int) -> ThreadingHTTPServer:
    """臨時起一個只服務本資料夾的伺服器（--serve 用）。"""
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(HERE), **k)

        def do_GET(self):
            if self.path in ("/", "/index.html", ""):
                self.path = "/dashboard.html"
            return super().do_GET()

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def wait_server(base: str, timeout: float = 6.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urlopen(base + "/dashboard.html", timeout=1).read(64)
            return True
        except Exception:
            time.sleep(0.25)
    return False


def shoot(chrome: str, url: str, w: int, h: int, out: Path, budget_ms: int):
    """用 headless Chrome 在 w×h 視窗截一張全頁長度的 PNG。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        "--force-device-scale-factor=1",
        "--default-background-color=00000000",
        f"--window-size={w},{h}",
        f"--virtual-time-budget={budget_ms}",  # 等字體/動畫/fetch state 完成
        f"--screenshot={out}",
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if not out.exists():
        print(f"[snapshot] 截圖失敗 {out.name}\n  stderr: {r.stderr[:400]}")
        return False
    print(f"[snapshot] OK {out.name}  ({w}x{h})")
    return True


def main():
    ap = argparse.ArgumentParser(description="數據獵手看板 IG/YT 快照")
    ap.add_argument("--url", default=None, help="看板基底網址，例：http://127.0.0.1:8910")
    ap.add_argument("--port", type=int, default=8910, help="搭配預設 127.0.0.1 的埠（預設 8910）")
    ap.add_argument("--mock", action="store_true", help="用 state.sample.json 渲染（測試）")
    ap.add_argument("--only", choices=["ig", "yt"], help="只截某一種尺寸")
    ap.add_argument("--out", default=str(HERE / "snapshots"), help="輸出資料夾")
    ap.add_argument("--chrome", default=None, help="指定 chrome.exe 路徑")
    ap.add_argument("--serve", action="store_true", help="自己臨時起伺服器再截（免另開 server）")
    ap.add_argument("--budget", type=int, default=2600, help="渲染等待毫秒（預設 2600）")
    args = ap.parse_args()

    chrome = find_chrome(args.chrome)
    base = args.url.rstrip("/") if args.url else f"http://127.0.0.1:{args.port}"

    httpd = None
    if args.serve:
        port = int(base.rsplit(":", 1)[-1]) if base.rsplit(":", 1)[-1].isdigit() else args.port
        httpd = start_local_server(port)
        base = f"http://127.0.0.1:{port}"
        print(f"[snapshot] 臨時伺服器 → {base}")

    if not wait_server(base):
        print(f"[snapshot] 連不到 {base} —— 請先 `python server.py {args.port}` 或加 --serve。")
        if httpd:
            httpd.shutdown()
        sys.exit(1)

    q = "?snapshot=1" + ("&mock=1" if args.mock else "")
    url = f"{base}/dashboard.html{q}"
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = Path(args.out)

    targets = [args.only] if args.only else ["ig", "yt"]
    ok = 0
    for key in targets:
        w, h = SIZES[key]
        out = out_dir / f"{key}_{stamp}.png"
        if shoot(chrome, url, w, h, out, args.budget):
            ok += 1

    if httpd:
        httpd.shutdown()
    print(f"[snapshot] 完成 {ok}/{len(targets)} 張 → {out_dir}")
    sys.exit(0 if ok == len(targets) else 2)


if __name__ == "__main__":
    main()
