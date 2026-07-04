# -*- coding: utf-8 -*-
"""
realtime_quote.py — 即時五檔／即時報價（對齊三竹的委買委賣五檔）

來源：證交所 MIS 即時資訊 API（mis.twse.com.tw getStockInfo，免費、免 key，
盤中約 20 秒延遲，與三竹免費版同級）。回傳成交價/漲跌/開高低昨收/漲跌停停
+ 委買委賣五檔（價+量）。上市(tse_)/上櫃(otc_)自動試。

＊此為官方公開延遲快照，非付費 tick 級行情；已是免費源能拿到的最好。

用法
  python realtime_quote.py 2330
"""
from __future__ import annotations

import json
import ssl
import sys
import urllib.request
from datetime import datetime

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_HDR = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://mis.twse.com.tw/stock/index.jsp"}
_MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _num(x):
    if x in (None, "", "-", "--"):
        return None
    try:
        return float(x)
    except (ValueError, TypeError):
        return None


def _levels(price_str: str, vol_str: str) -> list[dict]:
    """把 '2430_2435_..' + '204_663_..' 併成 [{price,vol}]（過濾空檔）。"""
    prices = [p for p in (price_str or "").split("_") if p]
    vols = [v for v in (vol_str or "").split("_") if v]
    out = []
    for i, p in enumerate(prices):
        pv = _num(p)
        if pv is None:
            continue
        out.append({"price": round(pv, 2),
                    "vol": int(_num(vols[i]) or 0) if i < len(vols) else 0})
    return out


def _parse(m: dict) -> dict:
    y = _num(m.get("y"))                       # 昨收
    z = _num(m.get("z"))                       # 成交價(可能 '-')
    # 成交價缺(盤前/無成交)時退用試撮/開盤/昨收，漲跌以昨收為基準
    px = z if z is not None else (_num(m.get("o")) or y)
    chg = (px - y) if (px is not None and y is not None) else None
    chg_pct = (chg / y * 100) if (chg is not None and y) else None
    return {
        "code": m.get("c"), "name": m.get("n"),
        "price": px, "prev_close": y,
        "chg": round(chg, 2) if chg is not None else None,
        "chg_pct": round(chg_pct, 2) if chg_pct is not None else None,
        "open": _num(m.get("o")), "high": _num(m.get("h")), "low": _num(m.get("l")),
        "limit_up": _num(m.get("u")), "limit_down": _num(m.get("w")),
        "volume": int(_num(m.get("v")) or 0),          # 累積成交張
        "tick_vol": int(_num(m.get("tv")) or 0),       # 當盤成交張
        "ask": _levels(m.get("a"), m.get("f")),        # 委賣五檔(由低到高)
        "bid": _levels(m.get("b"), m.get("g")),        # 委買五檔(由高到低)
        "time": _ts(m.get("tlong")),
        "traded": z is not None,                        # 是否已有成交
    }


def _ts(tlong) -> str | None:
    try:
        return datetime.fromtimestamp(int(tlong) / 1000).strftime("%H:%M:%S")
    except Exception:
        return None


def fetch_quote(code: str, timeout: int = 8) -> dict | None:
    """抓單檔即時報價+五檔。上市/上櫃自動試；抓不到回 None。"""
    ex = f"tse_{code}.tw|otc_{code}.tw"
    url = f"{_MIS}?ex_ch={ex}&json=1&delay=0&_={int(datetime.now().timestamp())}"
    try:
        req = urllib.request.Request(url, headers=_HDR)
        d = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_CTX)
                       .read().decode("utf-8", "replace"))
    except Exception:
        return None
    if d.get("rtcode") != "0000":
        return None
    arr = d.get("msgArray") or []
    # 取有昨收(=有效標的)且優先有五檔的那筆
    valid = [m for m in arr if _num(m.get("y")) is not None]
    if not valid:
        return None
    valid.sort(key=lambda m: (0 if (m.get("b") or m.get("a")) else 1))
    return _parse(valid[0])


_INTRA_CACHE: dict = {}          # code → (monotonic_ts, closes)；分時 yfinance 較慢，60 秒快取省重抓


def fetch_intraday(code: str) -> list | None:
    """今日分時走勢：yfinance 1 分K 收盤序列(約延遲15分，給分時線)。上市→.TW 上櫃→.TWO 自動試。
    60 秒快取：分時本就延遲，重開同檔不必重抓。"""
    import time as _t
    hit = _INTRA_CACHE.get(code)
    if hit and (_t.monotonic() - hit[0]) < 60:
        return hit[1]
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
    except Exception:
        return None
    for suf in (".TW", ".TWO"):
        try:
            h = yf.Ticker(code + suf).history(period="1d", interval="1m")
            closes = [round(float(x), 2) for x in h["Close"].dropna().tolist()]
            if len(closes) >= 2:
                _INTRA_CACHE[code] = (_t.monotonic(), closes)
                return closes
        except Exception:
            continue
    return None


# ── 大盤主要指數群(證交所 MIS) + 國際指數(yfinance) ──────────────────────────
# MIS 指數代碼：t00 加權、o00 櫃買(上櫃)、t13 電子、t21 金融、t24 半導體
_TWSE_INDICES = [("t00", "加權指數"), ("o00", "櫃買指數"), ("t13", "電子類"),
                 ("t21", "金融保險"), ("t24", "半導體")]
_INTL = [("^DJI", "道瓊"), ("^IXIC", "那斯達克"), ("^SOX", "費半"), ("^N225", "日經")]


def fetch_indices(timeout: int = 8) -> list[dict]:
    """台股主要指數群(MIS，約20秒延遲)。回 [{name, price, chg, chg_pct}]。"""
    ex = "|".join((f"otc_{c}.tw" if c.startswith("o") else f"tse_{c}.tw")
                  for c, _ in _TWSE_INDICES)
    url = f"{_MIS}?ex_ch={ex}&json=1&delay=0&_={int(datetime.now().timestamp())}"
    try:
        req = urllib.request.Request(url, headers=_HDR)
        d = json.loads(urllib.request.urlopen(req, timeout=timeout, context=_CTX)
                       .read().decode("utf-8", "replace"))
    except Exception:
        return []
    if d.get("rtcode") != "0000":
        return []
    by_code = {m.get("c"): m for m in d.get("msgArray") or []}
    out = []
    for code, name in _TWSE_INDICES:
        m = by_code.get(code)
        if not m:
            continue
        y = _num(m.get("y")); z = _num(m.get("z"))
        px = z if z is not None else _num(m.get("o")) or y
        chg = (px - y) if (px is not None and y is not None) else None
        out.append({"name": name, "price": round(px, 2) if px is not None else None,
                    "chg": round(chg, 2) if chg is not None else None,
                    "chg_pct": round(chg / y * 100, 2) if (chg is not None and y) else None})
    return out


def fetch_international(timeout: int = 10) -> list[dict]:
    """國際指數(yfinance，非即時)：道瓊/那指/費半/日經。回 [{name, price, chg_pct}]。"""
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
    except Exception:
        return []
    out = []
    for tk, name in _INTL:
        try:
            h = yf.Ticker(tk).history(period="2d")["Close"].dropna()
            if len(h) >= 2:
                px, prev = float(h.iloc[-1]), float(h.iloc[-2])
                out.append({"name": name, "price": round(px, 2),
                            "chg_pct": round((px / prev - 1) * 100, 2)})
        except Exception:
            continue
    return out


if __name__ == "__main__":
    if "--indices" in sys.argv:
        print("台股主要指數:")
        for x in fetch_indices():
            print(f"  {x['name']:6} {x['price']}  {x['chg_pct']:+}%")
        print("國際指數:")
        for x in fetch_international():
            print(f"  {x['name']:6} {x['price']}  {x['chg_pct']:+}%")
        raise SystemExit(0)
    code = sys.argv[1] if len(sys.argv) > 1 else "2330"
    q = fetch_quote(code)
    if not q:
        print("抓不到", code); raise SystemExit(1)
    print(f"{q['code']} {q['name']}  成交 {q['price']}  漲跌 {q['chg']}({q['chg_pct']}%)  量 {q['volume']} 張  {q['time'] or ''}")
    print("  委賣五檔(高→低):")
    for lv in reversed(q["ask"]):
        print(f"    {lv['price']:>9}  {lv['vol']:>6} 張")
    print("  ── 成交 {} ──".format(q["price"]))
    print("  委買五檔(高→低):")
    for lv in q["bid"]:
        print(f"    {lv['price']:>9}  {lv['vol']:>6} 張")
