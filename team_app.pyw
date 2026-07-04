# -*- coding: utf-8 -*-
"""
team_app.pyw — 金融分析團隊 桌面 app
雙擊即啟動：背景跑 team_server，pywebview 開原生視窗
"""
import sys
import threading
import time
import socket
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

PORT = 8900


def _find_free_port(start: int) -> int:
    for p in range(start, start + 20):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    return start


def _wait_server(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _start_server(port: int) -> None:
    from http.server import ThreadingHTTPServer
    import team_server
    team_server.TEAM_HTML = HERE / "team.html"
    team_server.STATE_FILE = HERE / "state.json"
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", port), team_server.TeamHandler)
    except OSError:
        return
    httpd.serve_forever()


def main():
    import webview

    port = _find_free_port(PORT)

    # 先確認 server 沒在跑，才起新的
    if not _wait_server(port, timeout=0.5):
        t = threading.Thread(target=_start_server, args=(port,), daemon=True)
        t.start()
        if not _wait_server(port, timeout=12):
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk(); root.withdraw()
            messagebox.showerror("啟動失敗", "無法啟動後端伺服器，請檢查 Python 環境")
            return

    url = f"http://127.0.0.1:{port}"
    window = webview.create_window(
        "金融分析團隊 · 台股監控",
        url,
        width=1440,
        height=900,
        min_size=(900, 600),
        background_color="#090d18",
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()
