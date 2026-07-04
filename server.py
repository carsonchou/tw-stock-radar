# -*- coding: utf-8 -*-
"""
server.py — 數據獵手看板伺服器（純標準庫，無相依）

開 http://127.0.0.1:8899 看深色 HUD 看板。
看板每 30 秒抓 state.json；state.json 由 scan.py / loop.py 在背景更新。

用法：
  python server.py            # 開在 8899
  python server.py 9000       # 自訂埠
  python server.py --scan     # 開站前先即時掃一輪(產出 state.json)
"""
from __future__ import annotations

import json
import sys
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

HERE = Path(__file__).resolve().parent
_INDICES_CACHE: dict = {}          # /api/indices 60 秒 module 快取


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(HERE), **k)

    def _send_json(self, obj, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.end_headers()
        self.wfile.write(body)

    def _handle_api(self, path: str, qs: dict) -> bool:
        """動態 API：/api/stock、/api/search、/api/analyst。命中回 True(已回應)，否則 False(交還靜態服務)。
        query/analyst 在 handler 內 import(而非模組頂層)，讓查價/分析失敗絕不拖垮靜態看板服務。"""
        if path not in ("/api/stock", "/api/search", "/api/analyst", "/api/news",
                        "/api/quote", "/api/indices", "/api/zones"):
            return False
        try:
            import query
        except Exception as e:                       # query 相依缺失 → 只影響 API，不影響看板
            self._send_json({"ok": False, "error": f"query 模組載入失敗：{e}"}, status=500)
            return True

        def _first(key: str) -> str:
            v = qs.get(key)
            return (v[0] if v else "").strip()

        try:
            if path == "/api/search":
                # 前端契約：直接回 JSON 陣列 [{code,name,industry}]；空 q 或出錯回 []（前端好迭代）
                q = _first("q")
                self._send_json(query.search_stocks(q) if q else [])
                return True

            if path == "/api/zones":
                # 交易專區(當沖/短線/長線)：讀盤後/背景產生的快取 zones.json
                try:
                    import zones
                    z = zones.load_zones()
                except Exception:
                    z = None
                self._send_json({"ok": bool(z), **(z or {})})
                return True

            if path == "/api/indices":
                # 大盤主要指數群 + 國際指數；60 秒 module 快取(避免每次輪詢重抓 yfinance)
                import time as _t
                global _INDICES_CACHE
                hit = _INDICES_CACHE.get("v")
                if hit and (_t.monotonic() - hit[0]) < 60:
                    self._send_json(hit[1]); return True
                from concurrent.futures import ThreadPoolExecutor
                twse = intl = []
                try:
                    import realtime_quote as _rq
                    with ThreadPoolExecutor(max_workers=2) as ex:
                        ft = ex.submit(_rq.fetch_indices)
                        fi = ex.submit(_rq.fetch_international)
                        try: twse = ft.result(timeout=8.0) or []
                        except Exception: twse = []
                        try: intl = fi.result(timeout=10.0) or []
                        except Exception: intl = []
                except Exception:
                    pass
                payload = {"ok": True, "twse": twse, "intl": intl}
                _INDICES_CACHE["v"] = (_t.monotonic(), payload)
                self._send_json(payload); return True

            if path == "/api/quote":
                # 即時五檔/報價(證交所 MIS，免費約20秒延遲)：?code= 或 ?q=
                from concurrent.futures import ThreadPoolExecutor
                raw = _first("code") or _first("q")
                code = query._resolve_code(raw) or raw
                q = None; intraday = None
                try:
                    import realtime_quote
                    with ThreadPoolExecutor(max_workers=2) as ex:
                        fq = ex.submit(realtime_quote.fetch_quote, code)
                        fi = ex.submit(realtime_quote.fetch_intraday, code)
                        try:
                            q = fq.result(timeout=8.0)
                        except Exception:
                            q = None
                        try:
                            intraday = fi.result(timeout=8.0)   # 分時走勢(慢一點沒關係)
                        except Exception:
                            intraday = None
                except Exception:
                    q = None
                self._send_json({"ok": bool(q), "quote": q, "intraday": intraday})
                return True

            if path == "/api/news":
                # 個股新聞(Google News RSS)：?code=&name=；有界抓取、短快取，抓不到回空陣列
                from concurrent.futures import ThreadPoolExecutor
                raw = _first("code") or _first("q")
                name = _first("name")
                code = query._resolve_code(raw) or raw
                if not name:
                    try:
                        name = query._meta(code)[0]
                    except Exception:
                        name = ""
                try:
                    import news
                    with ThreadPoolExecutor(max_workers=1) as ex:
                        items = ex.submit(news.load_news, name, code, False, 8).result(timeout=8.0)
                except Exception:
                    items = []
                self._send_json({"ok": True, "items": items or []})
                return True

            if path == "/api/analyst":
                # 金融分析團隊四維度：支援 ?code= 或 ?q=(名稱)。名稱先用 query._resolve_code 轉代號，
                # 再交 analyst.analyze_one(內含四維分析+綜合操作策略)。有界執行(~12s)避免慢網卡死。
                from concurrent.futures import ThreadPoolExecutor
                raw = _first("code") or _first("q")
                if not raw:
                    self._send_json({"ok": False, "error": "缺少 code 或 q"}, status=400)
                    return True
                code = query._resolve_code(raw) or raw
                import analyst
                with ThreadPoolExecutor(max_workers=1) as ex:
                    res = ex.submit(analyst.analyze_one, code).result(timeout=12.0)
                if res is None:
                    self._send_json({"ok": False, "error": f"{code} 無法取得資料"}, status=404)
                else:
                    self._send_json({"ok": True, **res}, status=200)
                return True

            # /api/stock：支援 ?code= 或 ?q=(名稱)；live=1 用即時價
            code = _first("code") or _first("q")
            if not code:
                self._send_json({"ok": False, "error": "缺少 code 或 q"}, status=400)
                return True
            live = _first("live") in ("1", "true", "yes")
            res = query.analyze_stock(code, live=live)
            self._send_json(res, status=200 if res.get("ok") else 404)
        except Exception as e:                        # 任意查詢例外都收斂成 JSON，server 不崩
            self._send_json({"ok": False, "error": f"{type(e).__name__}: {e}"}, status=500)
        return True

    def do_GET(self):
        split = urlsplit(self.path)
        if self._handle_api(split.path, parse_qs(split.query)):
            return
        if self.path in ("/", "/index.html", ""):
            self.path = "/dashboard.html"
        return super().do_GET()

    def end_headers(self):
        # state.json 不要被快取
        if self.path.startswith("/state.json"):
            self.send_header("Cache-Control", "no-store, max-age=0")
        super().end_headers()

    def log_message(self, fmt, *args):
        pass  # 安靜


def main():
    port = 8899
    do_scan = False
    for a in sys.argv[1:]:
        if a == "--scan":
            do_scan = True
        elif a.isdigit():
            port = int(a)

    if do_scan:
        try:
            import scan
            print("[server] 開站前先掃一輪…")
            scan.run_once(push=False)
        except Exception as e:
            print(f"[server] 預掃失敗（仍照常開站）：{e}")

    # 埠占用 → 自動 +1 重試(比照 app.py 捕捉 OSError)，最多試 10 個埠
    httpd = None
    for p in range(port, port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            port = p
            break
        except OSError:
            print(f"[server] 埠 {p} 已被占用，改試 {p + 1}…")
            continue
    if httpd is None:
        print(f"[server] 連續 10 個埠({port}-{port + 9})皆被占用，放棄。")
        return

    url = f"http://127.0.0.1:{port}/"
    print(f"[server] 數據獵手看板 → {url}")
    print("[server] Ctrl+C 結束")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[server] 已停止")


if __name__ == "__main__":
    main()
