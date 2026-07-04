# -*- coding: utf-8 -*-
"""
health.py — 個股健診評分引擎（對齊三竹「六宮格：財務+籌碼+技術」）

取代舊評分的病灶：四師權重拍腦袋+重複硬編碼、二元階梯計分無鑑別度、
完全沒有基本面、scan 強弱分與 analyst 總分兩套口徑打架。

本引擎：四大面向連續計分（非門檻跳階），單一權重來源，等級 A–E + 信心度。
四師分析保留為「敘事/手把手教學」層不動；此健診是頭條的客觀量化評分。

輸入 = query.analyze_stock 的統一個股 dict（已含 技術指標 + 籌碼 + 基本面）。
輸出 = {overall, grade, confidence, pillars:{技術/籌碼/基本面/估值}{score,items,weight,has_data}}。

計分哲學
  連續正規化：_squash(線性夾擠) / _logistic(S 型)，門檻附近平滑、有鑑別度。
  缺資料不靜默給 0：該面向 has_data=False、退出加權（權重重分配給有資料面向），
    並反映在 confidence（有資料面向的權重總和）。
"""
from __future__ import annotations

import math

# ── 單一權重來源（修掉舊碼硬編碼兩份問題）──────────────────────────────────────
# 波段偏好：技術主導、籌碼次之、基本面/估值為底。可調，但集中一處。
HEALTH_WEIGHTS = {"技術": 0.35, "籌碼": 0.30, "基本面": 0.20, "估值": 0.15}


# ── 連續正規化工具 ────────────────────────────────────────────────────────────
def _squash(x, lo, hi):
    """線性夾擠到 0..1；x<=lo→0, x>=hi→1。x=None→None。"""
    if x is None:
        return None
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _logistic(x, mid, k):
    """S 型 0..1；mid 為 0.5 點，k 控斜率。x=None→None。"""
    if x is None:
        return None
    try:
        return 1.0 / (1.0 + math.exp(-k * (x - mid)))
    except OverflowError:
        return 0.0 if x < mid else 1.0


def _avg(parts):
    """對 (weight, value0_1) 清單做加權平均，忽略 value=None 的項；全 None→None。"""
    num = den = 0.0
    for w, v in parts:
        if v is None:
            continue
        num += w * v
        den += w
    return (num / den) if den > 0 else None


def _g(d, *keys):
    """取第一個非 None 的鍵值。"""
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


# ── 面向計分（各回 (score0_100 或 None, items[]) ）──────────────────────────────
def _pillar_tech(d):
    """技術面：趨勢(SuperTrend/ADX) + 動能(RSI/MACD) + 位階(均線/%B)。"""
    st = d.get("st")
    st_up = 1.0 if st == "UP" else (0.0 if st == "DOWN" else (0.5 if st else None))
    adx = _squash(d.get("adx"), 15, 45)
    trend = _avg([(0.5, st_up), (0.5, adx)])

    rsi = _squash(d.get("rsi"), 45, 72)
    macd_v = _g(d, "macd_hist", "macd")
    macd_up = None if (macd_v is None and d.get("macd_trend") is None) else \
        (1.0 if (d.get("macd_trend") in ("up", "多", True) or (macd_v or 0) > 0) else 0.0)
    momentum = _avg([(0.6, rsi), (0.4, macd_up)])

    a20 = d.get("above20"); a60 = d.get("above60")
    align = None if (a20 is None and a60 is None) else \
        ((1.0 if a20 else 0.0) * 0.5 + (1.0 if a60 else 0.0) * 0.5)
    pb = _squash(_g(d, "pct_b", "percent_b"), 0.2, 0.9)
    position = _avg([(0.5, align), (0.5, pb)])

    score01 = _avg([(0.40, trend), (0.35, momentum), (0.25, position)])
    items = [
        _item("趨勢 SuperTrend/ADX", _fmt_st(d.get("st"), d.get("adx")), trend),
        _item("動能 RSI/MACD", _fmt_num(d.get("rsi"), "RSI "), momentum),
        _item("位階 均線/%B", _fmt_align(a20, a60), position),
    ]
    return (round(score01 * 100, 1) if score01 is not None else None), items


def _pillar_chip(d):
    """籌碼面：三大法人連買/淨買 + 投信 + 融資券 + 集保散戶。"""
    consec = _squash(d.get("consec_buy_days"), 0, 7)
    inet = d.get("instinv_net")
    inet_sign = None if inet is None else (1.0 if inet > 0 else (0.0 if inet < 0 else 0.5))
    inst = _avg([(0.6, consec), (0.4, inet_sign)])

    trust = _squash(d.get("trust_consec_days"), 0, 5)

    mc = d.get("margin_chg")
    finance = None if mc is None else (0.7 if mc < 0 else (0.35 if mc > 0 else 0.5))
    sr = d.get("short_margin_ratio")
    if sr is not None:  # 券資比高=空方壓力，扣一點
        finance = (finance if finance is not None else 0.5) * (1.0 - min(0.3, sr / 100))

    retail = None
    if d.get("retail_exit"):
        retail = 0.85
    elif d.get("retail_surge"):
        retail = 0.3
    elif d.get("small_chg_pct") is not None:
        retail = _squash(-d.get("small_chg_pct"), -3, 3)  # 散戶減(負變化)→偏多

    score01 = _avg([(0.5, inst), (0.2, trust), (0.15, finance), (0.15, retail)])
    has = any(v is not None for v in (inst, trust, finance, retail))
    items = [
        _item("三大法人 連買/淨買", _fmt_inst(d.get("consec_buy_days"), inet), inst),
        _item("投信 連買", _fmt_days(d.get("trust_consec_days")), trust),
        _item("融資券 變化", _fmt_num(mc, "融資 ", " 張"), finance),
        _item("集保 散戶動向", _fmt_retail(d), retail),
    ]
    return (round(score01 * 100, 1) if score01 is not None else None), items, has


def _roe_est(d):
    """ROE 免額外抓取推估：BVPS=price/PB → ROE≈EPS_ttm/BVPS=EPS_ttm×PB/price(%)。
    2330 實測 74.39×11.03/2465≈33% 對得上官方。缺任一→None。"""
    eps, pb, px = d.get("eps_ttm"), d.get("pb"), d.get("price")
    if eps is None or not pb or not px or px <= 0:
        return None
    return round(eps * pb / px * 100, 1)


def _pillar_fund(d):
    """基本面：EPS 獲利/成長 + 營收 YoY + 毛利率 + ROE(推估)。"""
    eps_ttm = d.get("eps_ttm")
    eps_pos = None if eps_ttm is None else (1.0 if eps_ttm > 0 else 0.0)
    eps_g = _squash(d.get("eps_yoy"), -10, 50)
    profit = _avg([(0.5, eps_pos), (0.5, eps_g)])

    rev = _squash(d.get("rev_yoy"), -10, 40)
    margin = _squash(d.get("gross_margin"), 5, 40)
    roe = _roe_est(d)
    roe_s = _squash(roe, 5, 25)                       # ROE 5%→0, 25%→1(台股績優門檻)

    score01 = _avg([(0.32, profit), (0.28, rev), (0.20, margin), (0.20, roe_s)])
    has = any(v is not None for v in (profit, rev, margin, roe_s))
    items = [
        _item("EPS(近四季)", _fmt_num(eps_ttm, "", " 元"), profit),
        _item("營收年增 YoY", _fmt_pct(d.get("rev_yoy")), rev),
        _item("毛利率", _fmt_pct(d.get("gross_margin")), margin),
        _item("ROE(推估)", _fmt_pct(roe), roe_s),
    ]
    return (round(score01 * 100, 1) if score01 is not None else None), items, has


def _pillar_value(d):
    """估值面：本益比 PE(越低越好) + 股價淨值比 PB + 殖利率(越高越好)。"""
    pe = d.get("pe")
    v_pe = None if (pe is None or pe <= 0) else (1.0 - (_squash(pe, 8, 40) or 0))
    pb = d.get("pb")
    v_pb = None if (pb is None or pb <= 0) else (1.0 - (_squash(pb, 0.8, 5) or 0))
    v_yld = _squash(d.get("dividend_yield"), 0, 6)

    score01 = _avg([(0.4, v_pe), (0.3, v_pb), (0.3, v_yld)])
    has = any(v is not None for v in (v_pe, v_pb, v_yld))
    items = [
        _item("本益比 PE", _fmt_num(pe), v_pe),
        _item("股價淨值比 PB", _fmt_num(pb), v_pb),
        _item("殖利率", _fmt_pct(d.get("dividend_yield")), v_yld),
    ]
    return (round(score01 * 100, 1) if score01 is not None else None), items, has


# ── 主入口 ────────────────────────────────────────────────────────────────────
def compute_health(d: dict) -> dict:
    """對統一個股 dict 算四面向健診。缺資料面向退出加權、反映在 confidence。"""
    tech_s, tech_it = _pillar_tech(d)
    chip_s, chip_it, chip_has = _pillar_chip(d)
    fund_s, fund_it, fund_has = _pillar_fund(d)
    val_s, val_it, val_has = _pillar_value(d)

    pillars = {
        "技術": {"score": tech_s, "items": tech_it, "weight": HEALTH_WEIGHTS["技術"],
                 "has_data": tech_s is not None},
        "籌碼": {"score": chip_s, "items": chip_it, "weight": HEALTH_WEIGHTS["籌碼"],
                 "has_data": bool(chip_has and chip_s is not None)},
        "基本面": {"score": fund_s, "items": fund_it, "weight": HEALTH_WEIGHTS["基本面"],
                   "has_data": bool(fund_has and fund_s is not None)},
        "估值": {"score": val_s, "items": val_it, "weight": HEALTH_WEIGHTS["估值"],
                 "has_data": bool(val_has and val_s is not None)},
    }
    # 只用有資料的面向加權（權重重分配），overall 0..100
    parts = [(p["weight"], p["score"]) for p in pillars.values()
             if p["has_data"] and p["score"] is not None]
    wsum = sum(w for w, _ in parts)
    overall = round(sum(w * s for w, s in parts) / wsum, 1) if wsum > 0 else None
    confidence = round(wsum / sum(HEALTH_WEIGHTS.values()), 2) if wsum > 0 else 0.0

    return {"overall": overall, "grade": _grade(overall),
            "confidence": confidence, "pillars": pillars}


def _grade(x):
    if x is None:
        return "—"
    return "A" if x >= 80 else "B" if x >= 65 else "C" if x >= 50 else "D" if x >= 35 else "E"


# ── 顯示格式化 helper（給前端六宮格用；score01 → 0..100 顯示分）────────────────
def _item(label, value, score01):
    return {"label": label, "value": value,
            "score": (round(score01 * 100) if score01 is not None else None)}


def _fmt_num(x, pre="", suf=""):
    return None if x is None else f"{pre}{x:g}{suf}"


def _fmt_pct(x):
    return None if x is None else f"{x:+.1f}%" if x < 0 else f"{x:.1f}%"


def _fmt_days(x):
    return None if x in (None, 0) else f"{int(x)} 日"


def _fmt_st(st, adx):
    a = f"ADX {adx:.0f}" if adx is not None else ""
    s = {"UP": "多頭", "DOWN": "空頭"}.get(st, "中性")
    return f"{s} {a}".strip()


def _fmt_align(a20, a60):
    if a20 is None and a60 is None:
        return None
    tags = []
    tags.append("站上月線" if a20 else "破月線")
    tags.append("站上季線" if a60 else "破季線")
    return " · ".join(tags)


def _fmt_inst(consec, inet):
    parts = []
    if consec:
        parts.append(f"連買 {int(consec)} 日")
    if inet is not None:
        parts.append(f"淨買 {inet:+,} 張")
    return " · ".join(parts) if parts else None


def _fmt_retail(d):
    if d.get("retail_exit"):
        return "散戶流出(吸籌)"
    if d.get("retail_surge"):
        return "散戶大量進場"
    if d.get("small_chg_pct") is not None:
        return f"散戶週變化 {d['small_chg_pct']:+.1f}%"
    return None


if __name__ == "__main__":
    import sys
    import query
    code = sys.argv[1] if len(sys.argv) > 1 else "2330"
    st = query.analyze_stock(code)
    if not st.get("ok"):
        print("查詢失敗:", st.get("error")); raise SystemExit(1)
    h = compute_health(st)
    print(f"=== {code} {st.get('name','')} 健診 ===")
    print(f"總分 {h['overall']}  等級 {h['grade']}  信心度 {h['confidence']}")
    for name, p in h["pillars"].items():
        flag = "" if p["has_data"] else "（資料不足）"
        print(f"  [{name}] {p['score']}{flag}")
        for it in p["items"]:
            print(f"      - {it['label']}: {it['value']}  (分 {it['score']})")
