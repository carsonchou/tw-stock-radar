# -*- coding: utf-8 -*-
"""
zones.py — 交易專區：當沖 / 短線 / 長線 全市場選股（具名 setup + 風格評分 + 手把手）

三種交易風格用不同準則對全市場(~1900檔)評分排序，各出候選榜。每檔附：
  zscore(該風格 0-100 連續分)、命中的 setup tags、風格關鍵指標、手把手操作卡。

全市場較慢 → 盤後/背景產生、快取 twdata/zones.json 供前端 /api/zones 讀。
資料源全部重用既有：scan(指標/訊號/停損)、chips/margin(籌碼當沖)、fundamentals(估值/財報)。

＊誠實定位：多因子選股「參考」，非保證獲利、非投資建議。短線強弱分已由 calibrate
  驗證分位對未來報酬單調遞增；其餘 setup 為公認技術/籌碼/基本面規則。

用法
  python zones.py            # 精選宇宙(快)
  python zones.py --full     # 全市場 ~1900 檔
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ZONES_JSON = HERE / "state_zones.json"       # 與 state.json 同層；gitignore 執行期資料
TWDATA_ZONES = ROOT / "twdata" / "zones.json"

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import scan
import universe
from health import _squash

TOP_N = 15                                    # 每風格取前 N


def _amplitude(ohlc) -> float | None:
    """當日振幅% = (高−低)/前收。ohlc=[[o,h,l,c],...]。"""
    if not ohlc or len(ohlc) < 2:
        return None
    o, h, l, c = ohlc[-1]
    pc = ohlc[-2][3]
    return round((h - l) / pc * 100, 2) if pc else None


def _prev_day_surge(ohlc) -> bool:
    """前一日大漲爆量的續攻/隔日沖樣態：前一日漲≥5% 且今日守住(收≥前收)。"""
    if not ohlc or len(ohlc) < 3:
        return False
    prev_chg = (ohlc[-2][3] / ohlc[-3][3] - 1) * 100 if ohlc[-3][3] else 0
    hold = ohlc[-1][3] >= ohlc[-2][3]
    return prev_chg >= 5 and hold


def _sq(x, lo, hi):
    v = _squash(x, lo, hi)
    return v if v is not None else 0.0


def _r(x, n=2):
    return round(x, n) if x is not None else None


# ── 逐檔組合完整風格資料 ─────────────────────────────────────────────────────
def _enrich(stocks: list[dict], valu: dict) -> None:
    """把估值(PE/PB/殖利率) + 推估 EPS/ROE + 振幅 併進每檔(就地)。"""
    for s in stocks:
        v = valu.get(s["code"]) or {}
        pe = v.get("pe"); pb = v.get("pb"); dy = v.get("dividend_yield")
        s["pe"], s["pb"], s["dividend_yield"] = pe, pb, dy
        s["amplitude"] = _amplitude(s.get("ohlc"))
        # EPS 推估：BWIBBU 有 PE → EPS≈price/PE(全市場都有，補財報快取不足)
        s["eps_est"] = _r(s["price"] / pe, 2) if (pe and pe > 0) else None
        # ROE 推估：BVPS=price/PB → ROE≈EPS/BVPS=EPS×PB/price
        s["roe_est"] = _r(s["eps_est"] * pb / s["price"] * 100, 1) \
            if (s["eps_est"] and pb and s["price"]) else None
        # 併單檔財報快取(有預抓的才有；rev_yoy 補強長線)
        try:
            import fundamentals as _fd
            f = _fd.load_fundamentals(s["code"], offline=True)
            s["rev_yoy"] = f.get("rev_yoy")
            if f.get("eps_ttm") is not None:
                s["eps_est"] = f["eps_ttm"]           # 有真實 EPS 就用真的
        except Exception:
            s["rev_yoy"] = None


# ── 三風格候選建構 ───────────────────────────────────────────────────────────
def _daytrade(stocks: list[dict]) -> list[dict]:
    out = []
    for s in stocks:
        turn = s.get("turnover_60d")
        if not turn or turn < 1e8:                    # 流動性門檻
            continue
        dtp = s.get("day_trade_pct"); amp = s.get("amplitude"); rv = s.get("relvol")
        setups = []
        if (rv or 0) >= 2.0 and s.get("recent_high20") and s["price"] >= s["recent_high20"] * 0.99 \
                and (amp or 0) >= 3:
            setups.append("爆量突破")
        if _prev_day_surge(s.get("ohlc")):
            setups.append("強勢續攻")
        parts = [(0.35, _squash(dtp, 10, 40)), (0.30, _squash(amp, 2, 8)),
                 (0.20, _squash(rv, 1, 3)), (0.15, _squash(turn / 1e8, 1, 20))]
        num = sum(w * v for w, v in parts if v is not None)
        den = sum(w for w, v in parts if v is not None)
        z = round(num / den * 100, 1) if den else 0.0
        if z < 25 and not setups:
            continue
        px = s["price"]
        out.append({**_base(s), "zscore": z, "setups": setups,
                    "metrics": {"day_trade_pct": dtp, "amplitude": amp,
                                "relvol": _r(rv, 2), "amount": _r(turn / 1e8, 2)},
                    "play": {"entry": f"爆量帶量突破當日高，參考 {px} 附近",
                             "stop": f"−1.5% 或跌破當日均價（約 {_r(px * 0.985)}）",
                             "target": f"+2%/+3% 分批（{_r(px * 1.02)}/{_r(px * 1.03)}）",
                             "note": "當日平倉不留倉、量縮轉弱先出、嚴設停損不凹單"}})
    out.sort(key=lambda x: x["zscore"], reverse=True)
    return out[:TOP_N]


def _swing(stocks: list[dict]) -> list[dict]:
    out = []
    for s in stocks:
        score = s.get("score") or 0
        firm_long = bool(s.get("firm") and s.get("signal") == "long")
        consec = s.get("consec_buy_days") or 0
        rh = s.get("recent_high20")
        breakout = bool(s.get("above20") and rh and s["price"] >= rh * 0.99 and s.get("st") == "UP")
        setups = []
        if firm_long:
            setups.append("翻多起漲")
        if breakout:
            setups.append("突破前高")
        if consec >= 3 and score >= 55:
            setups.append("法人連買強勢")
        z = round(100 * (0.45 * (score / 100) + 0.20 * (1 if firm_long else 0)
                         + 0.20 * _sq(consec, 0, 7) + 0.15 * (1 if breakout else 0)), 1)
        if z < 35 and not setups:
            continue
        gap = _r((s["price"] / rh - 1) * 100, 1) if rh else None
        stop = s.get("stop"); tp1 = s.get("tp1"); tp2 = s.get("tp2")
        out.append({**_base(s), "zscore": z, "setups": setups,
                    "metrics": {"score": score, "st": s.get("st"), "gap_high": gap,
                                "consec": consec, "rsi": s.get("rsi"), "stop": stop},
                    "play": {"entry": f"回踩月線 MA20 分批 / 突破前高 {_r(rh)} 追",
                             "stop": f"{stop}（ATR 結構停損）" if stop else "跌破月線出",
                             "target": f"{tp1} / {tp2} 分批停利" if tp1 else "前高/滿足點分批",
                             "note": "持有數日~數週；破月線或 SuperTrend 反轉出場"}})
    out.sort(key=lambda x: x["zscore"], reverse=True)
    return out[:TOP_N]


def _longterm(stocks: list[dict]) -> list[dict]:
    out = []
    for s in stocks:
        pe = s.get("pe"); dy = s.get("dividend_yield"); eps = s.get("eps_est")
        roe = s.get("roe_est"); rev = s.get("rev_yoy"); a60 = s.get("above60")
        is_etf = str(s["code"]).startswith("00")
        setups = []
        if (dy or 0) >= 5 and (eps or 0) > 0 and a60:
            setups.append("高殖利率存股")
        if pe and 0 < pe <= 20 and (rev or 0) > 0 and (roe or 0) >= 10 and a60:
            setups.append("價值成長")
        if is_etf:
            setups.append("ETF專區")
        val = 0.5 * _sq(dy, 0, 6) + 0.5 * (1 - _sq(pe, 8, 40) if (pe and pe > 0) else 0)
        fund = (0.4 * (1 if (eps or 0) > 0 else 0) + 0.3 * _sq(rev, -10, 40) + 0.3 * _sq(roe, 5, 25))
        near_hi = _sq(s["price"], (s.get("lo60") or s["price"]), (s.get("hi60") or s["price"]))
        trend = 0.6 * (1 if a60 else 0) + 0.4 * near_hi
        z = round(100 * (0.30 * val + 0.35 * fund + 0.35 * trend), 1)
        if z < 35 and not setups:
            continue
        px = s["price"]
        out.append({**_base(s), "zscore": z, "setups": setups,
                    "metrics": {"pe": pe, "yield": dy, "eps": eps, "rev_yoy": rev,
                                "roe": roe, "above60": bool(a60)},
                    "play": {"entry": f"合理估值回檔分批，參考 {_r(px * 0.96)}~{px}",
                             "stop": "跌破年線 / EPS 轉衰再檢視",
                             "target": f"長抱，殖利率 {dy}% 存股" if dy else "長抱、隨基本面調整",
                             "note": "基本面(EPS/營收)維持成長、站穩季/年線才續抱"}})
    out.sort(key=lambda x: x["zscore"], reverse=True)
    return out[:TOP_N]


def _base(s: dict) -> dict:
    return {"code": s["code"], "name": s.get("name", ""), "industry": s.get("industry", ""),
            "price": s.get("price"), "chg": s.get("chg")}


# ── 主入口 ────────────────────────────────────────────────────────────────────
def build_zones(full: bool = False, use_cache_only: bool = True) -> dict:
    rows = universe.load_full_universe() if full else universe.all_codes()
    data = scan.load_universe_data(rows, use_cache_only=use_cache_only, intraday=False)
    name_of = {c: n for c, n, *_ in rows}
    ind_of = {c: (r[2] if len(r) > 2 else "") for r in rows for c in [r[0]]}
    stocks = []
    for code, df in data.items():
        core = scan._analyse_core(code, df, drop_last=False)
        if not core:
            continue
        core["code"] = code
        core["name"] = name_of.get(code, code)
        core["industry"] = ind_of.get(code, "")
        stocks.append(core)
    # 併籌碼/融資券/估值
    codes = [s["code"] for s in stocks]
    try:
        cm = scan.chips.load_chips(codes, days=scan.CHIP_DAYS, offline=True) if scan.chips else {}
    except Exception:
        cm = {}
    try:
        mm = scan.margin_mod.load_margin(codes, offline=True) if scan.margin_mod else {}
    except Exception:
        mm = {}
    for s in stocks:
        rec = cm.get(s["code"]) or {}
        s["consec_buy_days"] = rec.get("consec_buy_days")
        s["foreign_net"] = rec.get("foreign_net"); s["trust_net"] = rec.get("trust_net")
        m = mm.get(s["code"]) or {}
        vl = s.get("vol_lots") or 0
        lots = m.get("day_trade_lots")
        s["day_trade_pct"] = (round(lots / vl * 100, 1) if (lots and vl > 0 and lots / vl * 100 <= 100)
                              else None)
    try:
        import fundamentals as _fd
        valu = _fd.load_valuation(offline=True)
    except Exception:
        valu = {}
    _enrich(stocks, valu)

    zones = {"daytrade": {"cands": _daytrade(stocks)},
             "swing": {"cands": _swing(stocks)},
             "longterm": {"cands": _longterm(stocks)},
             "generated_at": datetime.now().isoformat(timespec="seconds"),
             "universe_n": len(stocks), "scope": "full" if full else "select"}
    _write(zones)
    return zones


def _write(zones: dict) -> None:
    for path in (ZONES_JSON, TWDATA_ZONES):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(zones, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:
            pass


def load_zones() -> dict | None:
    for path in (ZONES_JSON, TWDATA_ZONES):
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
    return None


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    full = "--full" in sys.argv
    t0 = time.time()
    z = build_zones(full=full)
    print(f"[zones] {z['scope']} {z['universe_n']} 檔，耗時 {time.time()-t0:.1f}s")
    for k, label in (("daytrade", "當沖"), ("swing", "短線"), ("longterm", "長線")):
        cs = z[k]["cands"]
        print(f"\n=== {label} top{min(5,len(cs))} ===")
        for c in cs[:5]:
            print(f"  {c['zscore']:5} {c['code']} {c['name'][:6]:6} "
                  f"setups={c['setups']} {c['metrics']}")
