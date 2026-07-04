# -*- coding: utf-8 -*-
"""
team_server.py — 金融分析團隊管理介面後端

路由：
  GET  /               → team.html
  GET  /state.json     → 最新市場掃描狀態（透傳）
  POST /api/analyze    → {"code":"2330"} → 四維度分析 JSON
  GET  /api/scan       → 精選宇宙四維共振 TOP 列表 JSON
  GET  /api/status     → 服務健康狀態

用法：
  python team_server.py           # 開在 8900
  python team_server.py 8901      # 自訂埠
  python team_server.py --scan    # 開站前先掃一輪
"""
from __future__ import annotations

import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_FILE = HERE / "state.json"
TEAM_HTML = HERE / "team.html"

# 四維分析快取（同一代號 120 秒內直接返回，不重算）
_ANALYZE_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 120


def _cors(h: BaseHTTPRequestHandler) -> None:
    h.send_header("Access-Control-Allow-Origin", "*")
    h.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    h.send_header("Access-Control-Allow-Headers", "Content-Type")


def _json_resp(h: BaseHTTPRequestHandler, obj, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-store")
    _cors(h)
    h.end_headers()
    h.wfile.write(body)


class TeamHandler(BaseHTTPRequestHandler):

    def do_OPTIONS(self):
        self.send_response(204)
        _cors(self)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?")[0]

        if path in ("/", "/team.html"):
            self._serve_file(TEAM_HTML, "text/html; charset=utf-8")

        elif path == "/state.json":
            self._serve_file(STATE_FILE, "application/json; charset=utf-8",
                             extra_headers={"Cache-Control": "no-store"})

        elif path == "/api/status":
            state_ok = STATE_FILE.exists()
            ts = None
            if state_ok:
                try:
                    ts = json.loads(STATE_FILE.read_text(encoding="utf-8")).get("ts")
                except Exception:
                    pass
            _json_resp(self, {"ok": True, "state_exists": state_ok,
                               "state_ts": ts, "server_time": time.strftime("%Y-%m-%dT%H:%M:%S")})

        elif path == "/api/scan":
            try:
                import analyst as _a
                top = _a.scan_universe(top_n=15)
                _json_resp(self, {"ok": True, "results": top})
            except Exception as e:
                _json_resp(self, {"ok": False, "error": str(e)}, 500)

        else:
            self.send_error(404)

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/analyze":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8") if length else "{}"
                payload = json.loads(body)
                code = str(payload.get("code", "")).strip().upper()
                if not code:
                    _json_resp(self, {"ok": False, "error": "缺少 code"}, 400)
                    return

                # 快取命中
                cached = _ANALYZE_CACHE.get(code)
                if cached and time.time() - cached[0] < _CACHE_TTL:
                    _json_resp(self, {"ok": True, "cached": True, "result": cached[1]})
                    return

                import analyst as _a
                result = _a.analyze_one(code)
                if result is None:
                    _json_resp(self, {"ok": False, "error": f"{code} 無法取得資料（可能代號錯誤或快取不足）"}, 404)
                    return

                _ANALYZE_CACHE[code] = (time.time(), result)
                _json_resp(self, {"ok": True, "cached": False, "result": result})

            except Exception as e:
                _json_resp(self, {"ok": False, "error": str(e)}, 500)

        else:
            self.send_error(404)

    def _serve_file(self, path: Path, content_type: str,
                    extra_headers: dict | None = None) -> None:
        if not path.exists():
            self.send_error(404, f"找不到 {path.name}")
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        _cors(self)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        try:
            msg = fmt % args
            if any(c in msg for c in ("200", "201", "404", "500")):
                print(f"[team] {msg}")
        except Exception:
            pass


def main():
    port = 8900
    do_scan = False
    for a in sys.argv[1:]:
        if a == "--scan":
            do_scan = True
        elif a.isdigit():
            port = int(a)

    if do_scan:
        try:
            import scan
            print("[team] 開站前先掃一輪市場…")
            scan.run_once(push=False)
        except Exception as e:
            print(f"[team] 預掃失敗（仍照常開站）：{e}")

    for attempt in range(10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port + attempt), TeamHandler)
            actual = port + attempt
            break
        except OSError:
            continue
    else:
        print("[team] 找不到可用的埠，結束")
        sys.exit(1)

    url = f"http://127.0.0.1:{actual}"
    print(f"[team] 金融分析團隊看板 → {url}")
    print(f"[team]   API: POST {url}/api/analyze  {{\"code\":\"2330\"}}")
    print(f"[team]   掃描: GET  {url}/api/scan")
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[team] 關站")


if __name__ == "__main__":
    main()
