# -*- coding: utf-8 -*-
"""
news.py — 個股新聞（對齊三竹個股新聞）

用 Google News RSS（免費、免 key）抓某檔個股近期新聞標題/來源/時間/連結。
非行情資料，走短快取(twdata/news/)避免每次查詢都打網。互動查詢有界、缺→空。

用法
  python news.py 台積電            # 顯示某股近期新聞
"""
from __future__ import annotations

import json
import re
import ssl
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
NEWS_DIR = ROOT / "twdata" / "news"
NEWS_DIR.mkdir(parents=True, exist_ok=True)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) DataHunter/1.0"}
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
CACHE_TTL_SEC = 900          # 新聞 15 分鐘快取


def _get(url: str, timeout: int = 12) -> str | None:
    try:
        req = urllib.request.Request(url, headers=_UA)
        return urllib.request.urlopen(req, timeout=timeout, context=_CTX).read().decode("utf-8", "replace")
    except Exception:
        return None


def _clean(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()


def fetch_news(name: str, code: str = "", limit: int = 8) -> list[dict]:
    """Google News RSS 抓個股新聞。回 [{title, source, time, link}]，抓不到回空。"""
    q = f"{name} 股票" if name else code
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q) +
           "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")
    xml = _get(url)
    if not xml:
        return []
    try:
        root = ET.fromstring(xml)
    except Exception:
        return []
    out = []
    for item in root.iter("item"):
        title = _clean(item.findtext("title"))
        link = _clean(item.findtext("link"))
        pub = _clean(item.findtext("pubDate"))
        src_el = item.find("source")
        source = _clean(src_el.text) if src_el is not None else ""
        # Google News 標題常是「標題 - 來源」：一律去掉尾綴(來源另欄顯示)，缺 source 時補用
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            if not source:
                source = tail
            title = head
        out.append({"title": _clean(title), "source": source,
                    "time": _fmt_time(pub), "link": link})
        if len(out) >= limit:
            break
    return out


def _fmt_time(pub: str) -> str:
    """RFC822 → 'MM/DD HH:MM'；解析失敗回原字串前段。"""
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S GMT"):
        try:
            dt = datetime.strptime(pub, fmt)
            return dt.strftime("%m/%d %H:%M")
        except Exception:
            continue
    return pub[:16]


def load_news(name: str, code: str, offline: bool = True, limit: int = 8) -> list[dict]:
    """短快取讀取；快取新鮮直接回，過期/缺且非 offline 才抓網。"""
    path = NEWS_DIR / f"{code or name}.json"
    if path.exists():
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
            if (time.time() - obj.get("ts", 0)) < CACHE_TTL_SEC:
                return obj.get("items", [])[:limit]
        except Exception:
            pass
    if offline and path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("items", [])[:limit]
        except Exception:
            return []
    if offline:
        return []
    items = fetch_news(name, code, limit)
    if items:
        try:
            path.write_text(json.dumps({"ts": time.time(), "items": items},
                                       ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
    return items


if __name__ == "__main__":
    nm = sys.argv[1] if len(sys.argv) > 1 else "台積電"
    for n in fetch_news(nm, limit=8):
        print(f"[{n['time']}] {n['title']}  ({n['source']})")
