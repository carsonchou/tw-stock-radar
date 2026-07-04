# -*- coding: utf-8 -*-
"""
analyst.py — 金融分析團隊：四維度選股分析器（深化版）

四個視角，四位老師「真方法論」的量化落地：
  朱家泓  — 技術面：均線糾結起漲 + KD 三招（黃金交叉/鈍化/背離）
  阿斯匹靈 — 籌碼面：投信作帳 + 三大法人同買 + 乖離溫度計
  權證小哥 — 主力K棒：光頭大紅棒發車 + 爆量量價 + 出貨訊號
  張捷    — 基本強勢：相對強弱(RS) + 站上季/年線 + 位階 + 法人認養

每個維度輸出 0-100 分 + 8~12 條「帶數字」判讀，最後給綜合評分、綜合操作策略
（買在哪/賣在哪/怎麼操作）與建議。

用法：
  python analyst.py 2330
  python analyst.py 2330 6669 3533
  python analyst.py --scan            # 掃精選宇宙，輸出四維共振候選股
  python analyst.py --push 2330       # 分析後推 Telegram
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
QS = HERE.parent
ROOT = QS.parent

sys.path.insert(0, str(QS))
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# ── 複用 scan.py 的資料層和指標函數 ───────────────────────────────────────
import scan as _scan

try:
    import chips as _chips
except Exception:
    _chips = None
try:
    import margin as _margin_mod
except Exception:
    _margin_mod = None
try:
    import tdcc as _tdcc_mod
except Exception:
    _tdcc_mod = None
try:
    from notify import broadcast as _broadcast
except Exception:
    _broadcast = None

CHIP_DAYS = 10


# ── 可調門檻常數（皆為「建議值，待回測校準」）───────────────────────────────
# 這些是四位老師方法論落地時的判定門檻，集中於此供之後 calibrate.py 回測微調。

# 朱家泓（技術面）
ZHU_CLUSTER_MAX = 0.03          # 均線糾結：max(MA5,10,20)−min /min ≤ 3%
ZHU_CLUSTER_MIN_BARS = 5        # 盤整 ≥ 5 根才算「糾結中」
ZHU_BREAK_BODY_MIN = 0.03       # 起漲突破紅K實體 ≥ 3%（相對前收）
ZHU_BREAK_VOL_MULT = 1.5        # 起漲量 ≥ 1.5× 20日均量
ZHU_BREAK_MAX_DAYS = 3          # 距突破 ≤ 3 天才算「剛起漲」
ZHU_KD_LOW = 30                 # KD 低檔（此下黃金交叉最強）
ZHU_KD_HIGH = 80                # KD 高檔（鈍化續抱/背離區）
ZHU_KD_DEEP_LOW = 20            # KD 深低檔（別接刀）
ZHU_DIVERGE_LOOKBACK = 20       # KD 背離回看根數

# 張捷（基本強勢）
ZHANG_RS_LOOKBACK = 20          # 相對強弱回看日數（個股 vs 大盤 0050）
ZHANG_RS_STRONG = 5.0           # 超額報酬 ≥ +5% 視為明顯強勢
ZHANG_VOL_MULT = 1.5            # 帶量門檻（站上季線需帶量）
ZHANG_YEARLINE_HOT = 0.50       # 距年線 > +50% 視為過熱（追高風險）
ZHANG_FOREIGN_CONSEC = 5        # 法人認養：外資/投信連買日門檻

# 阿斯匹靈（籌碼面）
ASP_TRUST_CONSEC = 3            # 投信連買 ≥ 3 日（作帳訊號起算）
ASP_BIAS_HOT = 0.05            # 月線乖離 > +5% 燒燙（別追）
ASP_BIAS_WARM = 0.02          # +2%~+5% 溫熱
ASP_BIAS_COLD = -0.02        # −2%~+2% 常溫；< −2% 偏冷（回檔承接）
ASP_BIAS_DEEP = -0.05        # < −5% 超跌（分批承接）
ASP_WINDOW_MONTHS = (3, 6, 9, 12)   # 季底作帳窗（下半月權重上調）

# 權證小哥（主力K棒）
WAR_BALD_GAIN = 0.05          # 光頭大紅棒漲幅 ≥ 5%
WAR_UPPER_SHADOW_MAX = 0.003 # 無上影：(high−close)/close < 0.3%
WAR_BODY_RATIO_MIN = 0.80    # 實體佔全K > 80%
WAR_VOL_STRONG = 3.0         # 爆量（強）≥ 3× 20日均量
WAR_VOL_BASE = 2.0           # 爆量 ≥ 2×
WAR_VOL_WATCH = 1.5          # 略增 ≥ 1.5×


# ── analyst 自足指標（不依賴 scan.py，避免耦合核心掃描檔）────────────────────
# 這幾支型態指標為 analyst 專用，實作於此以保持本模組自足；scan.py 完全不需改。
# 輸入 df 皆為「小寫欄位」的 OHLCV DataFrame（呼叫端已 _scan._lower 處理）。
def _kd_full(df: pd.DataFrame, n: int = 9,
             k_period: int = 3, d_period: int = 3) -> tuple[list, list]:
    """台股標準 KD 隨機指標 (9,3,3)，回傳整條 K、D 序列（list，與 df 對齊）。

    未起算的前置根填 None。給鈍化（連N根高/低檔）與背離判讀用。
    """
    if df is None or len(df) < n + 2:
        return [], []
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    ll = low.rolling(n).min()
    hh = high.rolling(n).max()
    rng = (hh - ll).replace(0, np.nan)
    rsv = ((close - ll) / rng * 100).clip(0, 100).fillna(50.0)

    a_k = 1.0 / k_period
    a_d = 1.0 / d_period
    k = d = 50.0
    ks: list = []
    ds: list = []
    for i in range(len(df)):
        if pd.isna(hh.iloc[i]) or pd.isna(ll.iloc[i]):
            ks.append(None)
            ds.append(None)
            continue
        k = k * (1 - a_k) + float(rsv.iloc[i]) * a_k
        d = d * (1 - a_d) + k * a_d
        ks.append(round(k, 2))
        ds.append(round(d, 2))
    return ks, ds


def _kd_last(df: pd.DataFrame, n: int = 9,
             k_period: int = 3, d_period: int = 3) -> tuple:
    """KD (9,3,3) 最後一根 (K, D, golden, death)。以 _kd_full 為底衍生，
    保留原簽名供其他呼叫端相容。資料不足回 (None, None, False, False)。"""
    ks, ds = _kd_full(df, n, k_period, d_period)
    valid = [i for i in range(len(ks)) if ks[i] is not None]
    if len(valid) < 2:
        return None, None, False, False
    i, j = valid[-1], valid[-2]
    k, d = ks[i], ds[i]
    prev_k, prev_d = ks[j], ds[j]
    golden = bool(prev_k <= prev_d and k > d)
    death = bool(prev_k >= prev_d and k < d)
    return k, d, golden, death


def _bald_red_k(df: pd.DataFrame, gain_thr: float = WAR_BALD_GAIN,
                vol_mult: float = WAR_VOL_BASE,
                upper_shadow_max: float = WAR_UPPER_SHADOW_MAX,
                body_ratio_min: float = WAR_BODY_RATIO_MIN,
                base_days: int = 15, base_range_max: float = 0.20) -> bool:
    """光頭大紅棒（權證小哥「主力發車」型態）。全部條件成立才 True：
      ① 今日漲幅 ≥ 5%（相對前一日收盤）
      ② 今量 ≥ 20日均量 × 2（爆量）
      ③ 無上影（收在高點=光頭）：(high−close)/close < 0.3%
      ④ 實體佔全K > 80%：|close−open|/(high−low) > 0.8
      ⑤ 前15日橫盤突破：前15日收盤（不含今日）高低差 < 20%
    「今日」= df 最後一列（呼叫端已裁成已收盤序列）。資料不足回 False。
    """
    if df is None or len(df) < 22:
        return False
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    openp = df["open"].astype(float)
    vol = df["volume"].astype(float)

    c = float(close.iloc[-1])
    pc = float(close.iloc[-2])
    h = float(high.iloc[-1])
    l = float(low.iloc[-1])
    o = float(openp.iloc[-1])
    if pc <= 0 or h <= l or c <= 0:
        return False

    # ① 今日漲幅 ≥ 5%
    if (c - pc) / pc < gain_thr:
        return False
    # ③ 無上影（光頭）
    if (h - c) / c >= upper_shadow_max:
        return False
    # ④ 實體佔全K > 80%
    if abs(c - o) / (h - l) <= body_ratio_min:
        return False
    # ② 爆量（今量 ≥ 20日均量 ×2）
    avg20 = float(vol.iloc[-21:-1].mean())
    if not (avg20 > 0 and float(vol.iloc[-1]) >= avg20 * vol_mult):
        return False
    # ⑤ 前15日橫盤（不含今日，收盤高低差 < 20%）
    base = close.iloc[-(base_days + 1):-1]
    lo = float(base.min())
    if lo <= 0 or (float(base.max()) - lo) / lo >= base_range_max:
        return False
    return True


# ── 均線糾結 / 起漲突破 / KD 背離（朱家泓專用型態偵測）──────────────────────
def _ma_cluster(close_s: pd.Series, bars: int = ZHU_CLUSTER_MIN_BARS) -> tuple:
    """均線糾結偵測。回傳 (糾結中 bool, 最新糾結比率%, 連續糾結根數)。
    糾結 = MA5/10/20 三線 (max−min)/min ≤ ZHU_CLUSTER_MAX 且連續 ≥ bars 根。"""
    if len(close_s) < 22:
        return False, None, 0
    ma5 = close_s.rolling(5).mean()
    ma10 = close_s.rolling(10).mean()
    ma20 = close_s.rolling(20).mean()
    window = min(max(bars, ZHU_CLUSTER_MIN_BARS) + 7, len(close_s))  # 看近 5~12 根
    ratios: list = []
    for i in range(-window, 0):
        vals = [ma5.iloc[i], ma10.iloc[i], ma20.iloc[i]]
        if any(pd.isna(v) for v in vals):
            ratios.append(None)
            continue
        lo = float(min(vals))
        ratios.append((float(max(vals)) - lo) / lo if lo > 0 else None)
    last = ratios[-1]
    consec = 0
    for r in reversed(ratios):
        if r is not None and r <= ZHU_CLUSTER_MAX:
            consec += 1
        else:
            break
    tangled = bool(last is not None and last <= ZHU_CLUSTER_MAX and consec >= bars)
    return tangled, (round(last * 100, 2) if last is not None else None), consec


def _recent_breakout(closed: pd.DataFrame, max_days: int = ZHU_BREAK_MAX_DAYS) -> tuple:
    """近 max_days 內是否『收盤上穿 MA5/10/20 群上緣』（起漲）。回傳 (突破 bool, 幾天前)。"""
    close = closed["close"].astype(float)
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    n = len(close)
    if n < 22:
        return False, None
    for back in range(0, max_days):
        i = n - 1 - back
        j = i - 1
        if j < 20:
            break
        hi_i = max(ma5.iloc[i], ma10.iloc[i], ma20.iloc[i])
        hi_j = max(ma5.iloc[j], ma10.iloc[j], ma20.iloc[j])
        if pd.isna(hi_i) or pd.isna(hi_j):
            continue
        if float(close.iloc[j]) <= float(hi_j) and float(close.iloc[i]) > float(hi_i):
            return True, back
    return False, None


def _kd_divergence(close_s: pd.Series, k_list: list,
                   lookback: int = ZHU_DIVERGE_LOOKBACK) -> str | None:
    """KD 背離。回傳 'bull'(底背離,買) / 'bear'(頂背離,賣) / None。
    底背離：價創 lookback 新低但 K 未創新低；頂背離：價創新高但 K 未創新高。"""
    ks = [x for x in k_list if x is not None]
    if len(close_s) < lookback or len(ks) < lookback:
        return None
    c = close_s.tail(lookback).to_numpy(dtype=float)
    kk = np.array(ks[-lookback:], dtype=float)
    c_now, k_now = c[-1], kk[-1]
    if c_now <= float(c.min()) * 1.002 and k_now > float(kk.min()) * 1.05:
        return "bull"
    if c_now >= float(c.max()) * 0.998 and k_now < float(kk.max()) * 0.95:
        return "bear"
    return None


# ── 大盤（0050）近 N 日報酬，供張捷相對強弱 RS 使用（當日記憶化）────────────
_MKT_CACHE: dict = {}


def _market_ret(n: int = ZHANG_RS_LOOKBACK) -> float | None:
    """大盤代理 0050 近 n 交易日報酬(%)。以「當日」記憶化，掃全宇宙時只讀一次快取。"""
    key = f"{date.today()}::{n}"
    if key in _MKT_CACHE:
        return _MKT_CACHE[key]
    ret: float | None = None
    try:
        df = _scan._read_cache(_scan.INDEX_CODE)
        if df is not None and len(df) > n:
            c = _scan._lower(df)["close"].astype(float).dropna()
            if len(c) > n:
                base = float(c.iloc[-1 - n])
                if base > 0:
                    ret = (float(c.iloc[-1]) - base) / base * 100
    except Exception:
        ret = None
    _MKT_CACHE[key] = ret
    return ret


# ── 操作區間計算 ──────────────────────────────────────────────────────────────
def _swing_low(close_s: pd.Series, n: int = 10) -> float | None:
    """近 n 日最低收盤（不含今日）作為短線支撐。"""
    window = close_s.iloc[-(n + 1):-1]
    return float(window.min()) if len(window) >= 3 else None


def _calc_zone(price: float, ma20: float | None, ma60: float | None,
               year_line: float | None, swing_lo: float | None,
               hi60: float | None, zone_type: str) -> dict | None:
    """
    各師操作區間統一計算入口。
    zone_type: 'zhu' | 'asp' | 'war' | 'zhang'
    回傳 {entry_lo, entry_hi, stop, target1, target2, rr}（rr=風報比）
    """
    if price <= 0:
        return None

    def _r(x): return round(x, 2)

    if zone_type == "zhu":
        # 朱家泓：MA20 附近分批，停損 MA60，目標 60日高或 +10%
        # 買區下緣＝回檔支撐(MA20)，但股價遠離 MA20(延伸股)時退回「現價 -7%」的可執行窄帶，
        # 避免下緣掉到 MA20 造成區間過寬/顛倒；上緣＝現價微上方。
        entry_hi = price * 1.01
        entry_lo = max(ma20 * 0.99 if ma20 else price * 0.97, price * 0.93)
        stop = max(ma60 * 0.99, price * 0.92) if ma60 else price * 0.93
        stop = min(stop, price * 0.93)
        target1 = hi60 * 1.02 if (hi60 and hi60 > price) else price * 1.10
        target2 = price * 1.18

    elif zone_type == "asp":
        # 阿斯匹靈：現價進，停損 MA20，目標 +15%/+25%（投信主導波段）
        entry_lo = price * 0.99
        entry_hi = price * 1.005
        stop = max(ma20 * 0.97, price * 0.92) if ma20 else price * 0.93
        stop = min(stop, price * 0.93)
        target1 = price * 1.15
        target2 = price * 1.25

    elif zone_type == "war":
        # 権証小哥：發車後回測進，停損當日低點，目標 1.5~2 倍漲幅
        entry_lo = price * 0.97  # 發車後回踩
        entry_hi = price * 1.02  # 或追當天收盤
        stop = max(swing_lo * 0.99, price * 0.92) if swing_lo else price * 0.93
        stop = min(stop, price * 0.93)
        target1 = price * 1.10
        target2 = price * 1.20

    elif zone_type == "zhang":
        # 張捷：回踩 MA20 進，停損年線，目標 60日高後再 +10%
        # 同 zhu：延伸股時下緣至多 -8%，避免下緣掉到 MA20 造成區間顛倒。
        entry_hi = price * 1.01
        entry_lo = max(ma20 * 0.99 if ma20 else price * 0.96, price * 0.92)
        stop = max(year_line * 0.98, price * 0.90) if year_line else price * 0.90
        stop = min(stop, price * 0.92)
        target1 = hi60 * 1.05 if (hi60 and hi60 > price) else price * 1.12
        target2 = price * 1.22

    else:
        return None

    # 健全性保證：極端價位/資料缺漏時保證區間合理，四師共用
    #  ①買區必遞增 ②停損必在買區下緣之下(risk>0) ③目標必在買區上緣之上(reward>0)
    entry_lo, entry_hi = min(entry_lo, entry_hi), max(entry_lo, entry_hi)
    stop = min(stop, entry_lo * 0.985)
    target1 = max(target1, entry_hi * 1.02)
    target2 = max(target2, target1 * 1.04)

    mid = (entry_lo + entry_hi) / 2
    reward = target1 - mid
    risk = mid - stop
    rr = round(reward / risk, 1) if risk > 0 else 0
    return {
        "entry_lo": _r(entry_lo), "entry_hi": _r(entry_hi),
        "stop": _r(stop), "target1": _r(target1), "target2": _r(target2),
        "rr": rr
    }


# ── 手把手教學（四師逐步操作教學 + 綜合 playbook）─────────────────────────────
# 這些函式「純用已算好的指標/籌碼/zone 生成文字」，不新造任何數字；價位全部來自
# df/zone/strategy。訊號弱（zone 為 None 或分數不足）時老實說「不宜進場」，不硬湊買點。
def _step(label: str, action: str, reason: str, price=None) -> dict:
    """組一個手把手教學步驟。price 可為 None（缺資料時該步不帶價）。
    label=第幾步做什麼、action=具體動作、price=關聯價位、reason=為什麼（老師口吻）。"""
    return {"step": label, "action": action, "price": price, "reason": reason}


def _teach_zhu(price, zone, ma10, ma20, ma60, launch, tangled, days_ago,
               above20, kd_k, div, score) -> list:
    """朱家泓手把手（技術均線起漲派）：均線糾結起漲、靠近均線進、乖離大不追。"""
    bias20 = (price - ma20) / ma20 if (ma20 and ma20 > 0) else None
    teach: list = []
    # 1) 現在能不能進場
    if score < 30 or not above20 or not zone:
        teach.append(_step(
            "1. 現在能不能進場", "不宜進場、先觀望",
            f"朱家泓：站不上月線 MA20({_fmt(ma20)}) 就是趨勢還沒轉多，這時候進場是逆勢接刀。"
            f"寧可空手等它站回均線、出現糾結後帶量紅K突破，才是我的起漲進場點。",
            _fmt(price)))
        return teach
    if bias20 is not None and bias20 > 0.10:
        teach.append(_step(
            "1. 現在能不能進場", "先別追、等回檔",
            f"朱家泓：現在離月線乖離 +{bias20*100:.0f}%，漲太多太遠。起漲要靠近均線進，"
            f"追高一拉回你就套在山頂，等它回檔靠近 MA10/MA20 再進。",
            _fmt(price)))
    elif launch:
        teach.append(_step(
            "1. 現在能不能進場", "可進場（起漲剛確認）",
            f"朱家泓：{days_ago}天前剛帶量突破均線群、紅K發動，這就是『起漲任意門』，"
            f"而且離均線還近、量價俱足，是標準進場時機。",
            _fmt(price)))
    elif tangled:
        teach.append(_step(
            "1. 現在能不能進場", "可分批佈局、等突破放大",
            "朱家泓：均線正在糾結蓄勢，像彈簧壓緊。先小量卡位，等它帶量紅K突破糾結區再加碼，"
            "突破才是真發動，別在盤整就重押。",
            _fmt(price)))
    else:
        teach.append(_step(
            "1. 現在能不能進場", "可留意、不強求",
            "朱家泓：站上月線、多頭格局還在，但沒有明確起漲K棒，進場就要嚴設停損，"
            "寧可等更漂亮的起漲訊號再重手。",
            _fmt(price)))
    # 2) 買點掛哪
    teach.append(_step(
        "2. 買點掛在哪", f"掛回檔支撐區限價 {zone['entry_lo']}~{zone['entry_hi']}",
        f"朱家泓：最佳買點是回踩月線/均線的支撐位置，成本低、停損近。用限價掛 "
        f"{zone['entry_lo']}~{zone['entry_hi']}，別用市價去追高。",
        f"{zone['entry_lo']}~{zone['entry_hi']}"))
    # 3) 分批
    teach.append(_step(
        "3. 怎麼分批", f"第一批 1/2 掛回檔區 {zone['entry_lo']}；第二批 1/2 突破 {zone['target1']} 再追",
        f"朱家泓：回檔到均線先佈一半，等真的帶量突破前高 {zone['target1']}、確認方向對了，"
        f"再把另一半加上去，順勢加碼不逆勢攤平。",
        f"{zone['entry_lo']} / {zone['target1']}"))
    # 4) 停損
    teach.append(_step(
        "4. 停損掛哪", f"跌破 {zone['stop']} 出場（結構停損）",
        f"朱家泓：停損放 MA60({_fmt(ma60)}) 下方或波段低點，跌破 {zone['stop']} 代表起漲結構被破壞、"
        f"趨勢轉弱，紀律停損認賠不凹單，硬上限 -10%。",
        _fmt(zone['stop'])))
    # 5) 停利
    t2_extra = ("；而且 KD 已現頂背離，追高風險高，到目標區要更果斷"
                if div == "bear" else "，到滿足點別貪、落袋為安")
    teach.append(_step(
        "5. 賣點停利", f"漲到 {zone['target1']} 先減 1/2、到 {zone['target2']} 全出",
        f"朱家泓：第一目標 {zone['target1']}（前高/壓力）先減碼落袋，剩下續抱看 "
        f"{zone['target2']}（滿足點）{t2_extra}。",
        f"{zone['target1']} / {zone['target2']}"))
    # 6) 情境應對
    kd_txt = f"或 KD 高檔死叉（K={kd_k:.0f}）" if kd_k is not None else ""
    teach.append(_step(
        "6. 之後怎麼應變",
        f"跌破 {zone['stop']} → 立刻走；站上 {zone['target1']} 帶量 → 續抱/加碼；爆量長黑{kd_txt} → 先減碼",
        "朱家泓：進場後只做三件事——跌破停損就走不囉嗦、帶量過前高就抱住讓獲利奔跑、"
        "出現爆量長黑或高檔背離就先保護獲利。",
        _fmt(zone['stop'])))
    return teach


def _teach_asp(price, zone, ma10, ma20, chips_rec, in_window, score) -> list:
    """阿斯匹靈手把手（籌碼作帳派）：跟投信作帳、法人買抱賣跑、乖離溫度計。"""
    bias20 = (price - ma20) / ma20 if (ma20 and ma20 > 0) else None
    tc = (chips_rec or {}).get("trust_consec_days") or 0
    teach: list = []
    # 1) 現在能不能進場
    if score < 25 or not zone:
        teach.append(_step(
            "1. 現在能不能進場", "籌碼面不宜進場",
            f"阿斯匹靈：法人沒連續買、投信也沒作帳（投信連買 {tc} 日），"
            f"沒有主力籌碼撐腰的票就是散戶在玩，籌碼派不追這種，先放生。",
            _fmt(price)))
        return teach
    if bias20 is not None and bias20 > 0.05:
        teach.append(_step(
            "1. 現在能不能進場", "別追高、等回月線",
            f"阿斯匹靈：乖離溫度計顯示離月線 +{bias20*100:.0f}% 已燒燙，投信作帳的票也怕追高點，"
            f"等它回檔貼近月線 MA20({_fmt(ma20)}) 再進，勝率高得多。",
            _fmt(price)))
    elif tc >= 3:
        win_txt = "、又逢季底作帳窗" if in_window else ""
        teach.append(_step(
            "1. 現在能不能進場", "可順著投信買（作帳波）",
            f"阿斯匹靈：投信已連買 {tc} 日{win_txt}，這是主力在做帳、有人幫你抬轎，"
            f"跟著投信方向做多勝算高，但要貼緊它的成本區、別追高。",
            _fmt(price)))
    else:
        teach.append(_step(
            "1. 現在能不能進場", "可低接、但要看到法人回補",
            f"阿斯匹靈：位階不貴可在月線附近承接，但要等投信/外資轉買（現投信連買 {tc} 日）"
            f"再加重，沒有法人買盤就輕倉試單。",
            _fmt(price)))
    # 2) 買點
    teach.append(_step(
        "2. 買點掛在哪", f"回檔 MA10({_fmt(ma10)}) 分批、區間 {zone['entry_lo']}~{zone['entry_hi']}",
        f"阿斯匹靈：強勢股不會讓你買在最低，回檔到月線/MA10 就是法人的成本區，"
        f"掛 {zone['entry_lo']}~{zone['entry_hi']} 限價、貼著投信成本進最安全。",
        f"{zone['entry_lo']}~{zone['entry_hi']}"))
    # 3) 分批
    teach.append(_step(
        "3. 怎麼分批", f"第一批 1/2 在 {zone['entry_lo']}~{zone['entry_hi']}；投信續買再加第二批",
        "阿斯匹靈：先進一半卡位，之後只要投信繼續買超、股價守穩月線，就把第二批加上去；"
        "一旦投信轉賣就停止加碼，籌碼會說話。",
        _fmt(zone['entry_lo'])))
    # 4) 停損
    teach.append(_step(
        "4. 停損掛哪", f"跌破 {zone['stop']}（月線下緣）出場",
        f"阿斯匹靈：停損放月線 MA20({_fmt(ma20)}) 下方 {zone['stop']}，跌破代表法人棄守、"
        f"作帳行情結束，先出場保本，籌碼壞了不留戀。",
        _fmt(zone['stop'])))
    # 5) 停利
    teach.append(_step(
        "5. 賣點停利", f"漲到 {zone['target1']} 減 1/2、{zone['target2']} 全出（投信轉賣先跑）",
        f"阿斯匹靈：目標 {zone['target1']} 先落袋一半，剩的看 {zone['target2']}；但作帳行情最關鍵的賣訊"
        f"是『投信由買轉賣』，看到投信倒貨不管到價沒到都先出。",
        f"{zone['target1']} / {zone['target2']}"))
    # 6) 情境
    teach.append(_step(
        "6. 之後怎麼應變",
        f"跌破 {zone['stop']} → 走；投信/外資轉賣 → 立刻減；站穩月線且法人續買 → 抱波段",
        "阿斯匹靈：籌碼派鐵律——法人買我就抱、法人賣我就跑，股價只是結果，"
        "三大法人的買賣超才是因，盯緊每天法人動向做調節。",
        _fmt(zone['stop'])))
    return teach


def _teach_war(price, zone, bald_red, relvol, day_low, long_upper, engulf, score) -> list:
    """權證小哥手把手（主力K棒/發車派）：光頭紅棒發車、回踩不破低點進、無量不追。"""
    rv = f"{relvol:.1f}×" if relvol is not None else "—"
    teach: list = []
    # 1) 現在能不能進場
    if long_upper or engulf:
        sig = "高檔長上影" if long_upper else "爆量收黑吞噬"
        teach.append(_step(
            "1. 現在能不能進場", "不要進、甚至該減碼",
            f"權證小哥：現在K棒出現{sig}，這是主力在出貨倒給散戶的訊號，"
            f"這時候進場等於接主力的貨，千萬別碰。",
            _fmt(price)))
        return teach
    if score < 15 or not zone:
        teach.append(_step(
            "1. 現在能不能進場", "沒主力、不進場",
            f"權證小哥：今天量能只有 {rv}、也沒有發車紅棒，沒有主力積極介入的股票拉不動，"
            f"我只做有主力發車的，這種先跳過。",
            _fmt(price)))
        return teach
    if bald_red:
        teach.append(_step(
            "1. 現在能不能進場", "可進、但等回踩不追高",
            f"權證小哥：今天是光頭大紅棒發車、量能 {rv}，主力積極拉抬方向明確。"
            f"但發車當天追高風險大，最好等隔天回踩不破紅棒低點再上車。",
            _fmt(price)))
    else:
        teach.append(_step(
            "1. 現在能不能進場", "可留意、要有量才進",
            f"權證小哥：目前量能 {rv}，主力介入跡象還不夠強。我的紀律是『無量不追』，"
            f"等看到爆量紅棒發車再進場才安全。",
            _fmt(price)))
    # 2) 買點
    teach.append(_step(
        "2. 買點掛在哪", f"回踩不破紅棒低點({_fmt(day_low)})進、區間 {zone['entry_lo']}~{zone['entry_hi']}",
        f"權證小哥：最安全的買點是發車後回測不破發車紅棒低點 {_fmt(day_low)}，"
        f"掛 {zone['entry_lo']}~{zone['entry_hi']}；回踩量縮不破就是洗盤，可上車。",
        f"{zone['entry_lo']}~{zone['entry_hi']}"))
    # 3) 分批
    teach.append(_step(
        "3. 怎麼分批", f"第一批 1/2 回踩進；第二批 1/2 帶量再過高點 {zone['target1']}",
        f"權證小哥：先進一半試單，確認回踩守住、再帶量創高 {zone['target1']} 表示主力還在拉，"
        f"才加第二批，順著主力方向加碼。",
        f"{_fmt(day_low)} / {zone['target1']}"))
    # 4) 停損
    teach.append(_step(
        "4. 停損掛哪", f"跌破發車紅棒低點 {zone['stop']} 出場",
        f"權證小哥：停損就掛在發車紅棒最低點 {zone['stop']}，跌破代表主力發車失敗、這根K是假的，"
        f"立刻走，這是主力K棒操作最硬的紀律。",
        _fmt(zone['stop'])))
    # 5) 停利
    teach.append(_step(
        "5. 賣點停利", f"漲到 {zone['target1']} 減 1/2、{zone['target2']} 全出；出長上影/爆量吞噬先跑",
        f"權證小哥：目標 {zone['target1']} 先減、{zone['target2']} 全出；但只要盤中出現高檔長上影或"
        f"爆量收黑吞噬（主力出貨），不管到價沒到都先落袋。",
        f"{zone['target1']} / {zone['target2']}"))
    # 6) 情境
    teach.append(_step(
        "6. 之後怎麼應變",
        f"跌破 {zone['stop']} → 走；帶量過 {zone['target1']} → 續抱加碼；爆量長上影/收黑吞噬 → 立刻減",
        "權證小哥：主力K棒只看量價——量增價漲主力還在就抱、跌破發車低點主力跑了就跟著跑、"
        "出現爆量長黑就是出貨要閃，跟著主力進退別自己想。",
        _fmt(zone['stop'])))
    return teach


def _teach_zhang(price, zone, ma60, yearline, rs_excess, score) -> list:
    """張捷手把手（基本強勢/RS 產業派）：買最強、回踩季線進、續抱吃主升段。"""
    dist_year = (price - yearline) / yearline if (yearline and yearline > 0) else None
    rs_str = f"（超額報酬 {rs_excess:+.0f}%）" if rs_excess is not None else ""
    teach: list = []
    # 1) 現在能不能進場
    if score < 25 or not zone:
        weak = rs_excess is not None and rs_excess < 0
        detail = f"弱於大盤{rs_str}" if weak else "強度/位階不夠"
        teach.append(_step(
            "1. 現在能不能進場", "不是強勢股、不進場",
            f"張捷：這檔{detail}，我只買最強、產業還在成長的領漲股，弱勢股再便宜也不碰，換強的做。",
            _fmt(price)))
        return teach
    if dist_year is not None and dist_year > 0.50:
        teach.append(_step(
            "1. 現在能不能進場", "位階偏高、等回檔或換股",
            f"張捷：離年線已 +{dist_year*100:.0f}%，位階太高（月盈則虧）。這時候不追，"
            f"要嘛等它回檔到季線附近、要嘛換一檔剛突破年線的強勢股。",
            _fmt(price)))
    else:
        teach.append(_step(
            "1. 現在能不能進場", "可進（強勢領漲、位階合理）",
            f"張捷：RS 相對強弱勝過大盤{rs_str}、又站在年線之上的合理位階，"
            f"這是我要的『買最強的』，可以進場續抱吃波段。",
            _fmt(price)))
    # 2) 買點
    teach.append(_step(
        "2. 買點掛在哪", f"回踩季線 MA60({_fmt(ma60)}) 分批、區間 {zone['entry_lo']}~{zone['entry_hi']}",
        f"張捷：強勢股要買在回檔、不追高，回踩季線 MA60({_fmt(ma60)}) 是主力洗盤的好買點，"
        f"掛 {zone['entry_lo']}~{zone['entry_hi']} 限價承接。",
        f"{zone['entry_lo']}~{zone['entry_hi']}"))
    # 3) 分批
    teach.append(_step(
        "3. 怎麼分批", f"第一批 1/2 回季線佈；第二批 1/2 帶量創高 {zone['target1']} 再加",
        f"張捷：先買一半底倉抱波段，等它帶量突破前高 {zone['target1']}、續強領漲時加碼，"
        f"強者恆強，加碼在創新高的強勢股身上、不在弱股攤平。",
        f"{zone['entry_lo']} / {zone['target1']}"))
    # 4) 停損
    teach.append(_step(
        "4. 停損掛哪", f"跌破季線 {zone['stop']}（或年線）出場",
        f"張捷：波段停損放季線 MA60({_fmt(ma60)})/年線 {zone['stop']}，跌破代表趨勢結構轉弱、"
        f"產業動能鈍化，該換股，結構停損不看短線雜訊。",
        _fmt(zone['stop'])))
    # 5) 停利
    teach.append(_step(
        "5. 賣點停利", f"漲到 {zone['target1']} 減 1/2、{zone['target2']} 全出；RS 轉弱或大盤破月線先減",
        f"張捷：目標 {zone['target1']} 先減、{zone['target2']} 續抱；但波段股最重要的賣訊是"
        f"『RS 由強轉弱（跑輸大盤）或大盤跌破月線』，趨勢轉了就獲利了結不留戀。",
        f"{zone['target1']} / {zone['target2']}"))
    # 6) 情境
    teach.append(_step(
        "6. 之後怎麼應變",
        f"跌破季線 {zone['stop']} → 換股；帶量創高 → 續抱加碼；RS 轉弱/大盤破月線 → 減碼",
        "張捷：續抱條件是『大盤未破月線 + 個股 RS>1（持續強於大盤）』，兩條還在就抱住吃主升段，"
        "一旦轉弱或大盤走空就先降部位保護獲利。",
        _fmt(zone['stop'])))
    return teach


def _build_playbook(price, closed, views, strategy, confirm, total) -> list:
    """整合四師 → 一份『今天到底怎麼操作』的白話總教學（3~6步，每步附理由）。
    純用 strategy(已彙整四師 zone) 與均線生成，不新造數字。"""
    if not strategy or price <= 0:
        return []
    close_s = closed["close"].astype(float)
    ma10 = float(close_s.rolling(10).mean().iloc[-1]) if len(close_s) >= 10 else None
    ma20 = float(close_s.rolling(20).mean().iloc[-1]) if len(close_s) >= 20 else None
    bias20 = (price - ma20) / ma20 if (ma20 and ma20 > 0) else None
    passed = [k for k, v in views.items() if v["score"] >= 50]
    passed_txt = "、".join(passed) if passed else "無"
    buy_lo, buy_hi = strategy["buy_zone_lo"], strategy["buy_zone_hi"]
    stop, t1, t2 = strategy["stop"], strategy["target1"], strategy["target2"]
    pos, rr = strategy["position_pct"], strategy["rr"]

    steps: list = []
    # ① 今天到底追不追
    if pos == 0:
        steps.append(_step(
            "① 今天怎麼看", "先不要進場、放觀察",
            f"四維只過 {confirm}/4（{passed_txt}），共振不足；沒有多位老師同時點頭的股票不急著買，"
            f"等技術起漲＋籌碼轉多再說，空手也是一種部位。",
            _fmt(price)))
        steps.append(_step(
            "② 等什麼訊號", f"等站上月線 MA20({_fmt(ma20)}) 帶量、且投信/外資轉買",
            "四位老師的共同底線是『趨勢＋籌碼』都要對，缺一就寧可空手等，不虧就是賺。",
            _fmt(ma20)))
        return steps
    hot = bias20 is not None and bias20 > 0.08
    if hot:
        steps.append(_step(
            "① 今天怎麼看", "今天不要追高",
            f"雖然過了 {confirm}/4 維（{passed_txt}），但離月線乖離 +{bias20*100:.0f}% 已燒燙，"
            f"四位老師都怕追在山頂，想買就等回檔到均線區。",
            _fmt(price)))
    else:
        steps.append(_step(
            "① 今天怎麼看", "可以分批進場",
            f"四維過 {confirm}/4（{passed_txt}）、位階不貴、方向偏多，可按紀律分批進場，"
            f"但一定要先設好停損。",
            _fmt(price)))
    # ② 想買掛哪
    steps.append(_step(
        "② 想買掛哪裡", f"掛回檔區 {buy_lo}~{buy_hi} 分兩批",
        f"別用市價追，掛在四位老師的共識進場區 {buy_lo}~{buy_hi}（回踩均線的成本區），"
        f"買得便宜停損才近、風報比才漂亮（目前 {rr}）。",
        f"{buy_lo}~{buy_hi}"))
    # ③ 怎麼分批
    steps.append(_step(
        "③ 怎麼分批進", f"第一批 1/2 回 MA10({_fmt(ma10)}) 佈、第二批 1/2 突破 {t1} 追",
        "先回檔佈一半底倉（成本低），等真的帶量突破前高、確認方向對了再加另一半，"
        "順勢加碼不逆勢攤平——這是四位老師共通的加碼紀律。",
        f"{_fmt(ma10)} / {t1}"))
    # ④ 停損
    steps.append(_step(
        "④ 停損掛哪裡", f"跌破 {stop} 立刻出場",
        f"停損放結構關卡 {stop}（月/季線或波段低點下方），跌破代表趨勢被破壞，"
        f"認賠不凹單、硬上限 -10%，留得青山在才有下次。",
        _fmt(stop)))
    # ⑤ 停利
    steps.append(_step(
        "⑤ 漲上去怎麼賣", f"到 {t1} 先減一半、到 {t2} 全出",
        f"第一目標 {t1}（前高壓力）先落袋一半降風險，剩下續抱看 {t2}（3R 滿足點），"
        f"獲利要讓它奔跑但也要分批落袋，別坐雲霄飛車。",
        f"{t1} / {t2}"))
    # ⑥ 出場鐵律
    steps.append(_step(
        "⑥ 什麼時候立刻出", f"跌破月線 MA20({_fmt(ma20)}) 或投信由買轉賣 → 不猶豫出場",
        "不管帳上賺賠，只要『跌破月線（趨勢轉弱）』或『投信/外資轉賣（籌碼變壞）』其中一個成立，"
        "就先出場保護資金——技術面、籌碼面任一破線都是離場訊號。",
        _fmt(ma20)))
    return steps


# ── 資料載入 ───────────────────────────────────────────────────────────────
def _load_ohlcv(code: str) -> pd.DataFrame | None:
    """快取優先（秒回）→ 快取過時（最後一筆距今 > 5 天）改抓官方最新。

    直接重用 query._load_df：其含「快取過時檢查 + 官方(twstock)最新價回補」邏輯，
    確保非精選股（盟立/微星等每日不刷新者）不會拿到數週前的舊價。
    query 不可用時退回原快取/yfinance 路徑。"""
    try:
        import query
        df = query._load_df(code, live=False)
        if df is not None and len(df) >= 30:
            return df
    except Exception:
        pass
    # 退路：query 不可用 → 讀快取，再退 yfinance
    df = _scan._read_cache(code)
    if df is not None and len(df) >= 30:
        return df
    for suf in (".TW", ".TWO"):
        res = _scan._bulk_yf([code], suf, intraday=False, retries=1)
        if res.get(code) is not None and len(res[code]) >= 30:
            return res[code]
    return None


def _load_chips_rec(code: str) -> dict | None:
    if _chips is None:
        return None
    try:
        # offline=True：只讀本地快取，絕不即時探網（比照 query.py 教訓，避免卡死）
        m = _chips.load_chips([code], days=CHIP_DAYS, offline=True)
        return m.get(code)
    except Exception:
        return None


def _load_margin_rec(code: str) -> dict | None:
    if _margin_mod is None:
        return None
    try:
        m = _margin_mod.load_margin([code], offline=True)   # 只讀快取，不探網
        return m.get(code)
    except Exception:
        return None


def _load_tdcc_rec(code: str) -> dict | None:
    if _tdcc_mod is None:
        return None
    try:
        m = _tdcc_mod.load_tdcc([code], offline=True)   # 只讀快取，不探 22 日網
        return m.get(code)
    except Exception:
        return None


def _closed_series(df: pd.DataFrame, tail: int = 180) -> pd.DataFrame:
    """統一取「已收盤」序列：小寫欄位、取尾段、若 >22 根則丟最後一根（未確認）。"""
    closed = _scan._lower(df.tail(tail).reset_index(drop=True))
    if len(closed) > 22:
        closed = closed.iloc[:-1]
    return closed


# ── 視角 1：朱家泓（技術：均線糾結起漲 + KD 三招）─────────────────────────
def _view_zhujiahe(code: str, df: pd.DataFrame) -> dict:
    """
    朱家泓真方法論落地：
      1) 均線糾結偵測（盤整蓄勢）→ 起漲突破（收盤>MA群+紅K+爆量+距突破≤3天）
      2) 多頭排列（MA5>10>20>60 完美 / 收盤>MA20&MA60 底線）
      3) KD 三招：①低檔黃金交叉 ②高/低檔鈍化 ③頂/底背離
    """
    closed = _closed_series(df, 180)

    ks, ds = _kd_full(closed)
    kd_k, kd_d, golden, death = _kd_last(closed)
    st_today, _ = _scan._st_dirs(closed)
    ma = _scan.calc_ma(closed, periods=[5, 10, 20, 60])
    ma5 = ma["ma"].get(5)
    ma10 = ma["ma"].get(10)
    ma20 = ma["ma"].get(20)
    ma60 = ma["ma"].get(60)
    rsi_val = _scan.calc_rsi(closed, period=14).get("rsi")
    close_s = closed["close"].astype(float)
    open_s = closed["open"].astype(float)
    vol_s = closed["volume"].astype(float)
    close_last = float(close_s.iloc[-1])

    above20 = ma20 is not None and close_last > ma20
    above60 = ma60 is not None and close_last > ma60
    st_up = (st_today == "UP")

    score = 0
    notes: list[str] = []

    # ── 招式一：均線糾結 → 起漲突破 ──────────────────────────────────
    tangled, cluster_pct, cluster_consec = _ma_cluster(close_s)
    broke, days_ago = _recent_breakout(closed)
    # 起漲確認：突破 + 紅K + 實體 + 量
    c_prev = float(close_s.iloc[-2]) if len(close_s) >= 2 else close_last
    body_pct = (close_last - float(open_s.iloc[-1])) / c_prev if c_prev else 0
    avg20v = float(vol_s.iloc[-21:-1].mean()) if len(vol_s) >= 22 else None
    vol_mult = (float(vol_s.iloc[-1]) / avg20v) if (avg20v and avg20v > 0) else None
    is_red = close_last > float(open_s.iloc[-1])
    launch = bool(broke and is_red and body_pct >= ZHU_BREAK_BODY_MIN
                  and vol_mult is not None and vol_mult >= ZHU_BREAK_VOL_MULT)
    if launch:
        score += 25
        notes.append(f"⚡ 起漲突破確認：{days_ago}天前上穿均線群，紅K實體 {body_pct*100:.1f}%、"
                     f"量 {vol_mult:.1f}×（朱家泓「起漲任意門」）")
    elif tangled:
        score += 10
        notes.append(f"✅ 均線糾結中（MA5/10/20 收斂 {cluster_pct:.1f}%、已 {cluster_consec} 根盤整）"
                     f"→ 蓄勢待突破，鎖定量增")
    elif broke:
        score += 12
        notes.append(f"⚠ {days_ago}天前突破均線群但量能/實體未達標（實體 {body_pct*100:.1f}%、"
                     f"量 {_fmt(vol_mult)}×）→ 假突破警戒")
    else:
        notes.append(f"— 未見糾結突破（近5根 MA 收斂 {cluster_pct if cluster_pct is not None else '—'}%）")

    # ── 招式二：多頭排列 ────────────────────────────────────────────
    perfect = all(x is not None for x in (ma5, ma10, ma20, ma60)) and ma5 > ma10 > ma20 > ma60
    if perfect:
        score += 20
        notes.append(f"✅ 完美多頭排列 MA5>10>20>60（{ma5:.1f}>{ma10:.1f}>{ma20:.1f}>{ma60:.1f}）")
    elif above20 and above60:
        score += 10
        notes.append(f"✅ 站上 MA20({ma20:.1f}) 與 MA60({ma60:.1f})（多頭底線成立）")
    elif above20:
        score += 5
        notes.append(f"⚠ 站上 MA20 但未站 MA60（{_fmt(ma60)}）→ 反彈格局")
    else:
        notes.append(f"❌ 跌破 MA20({_fmt(ma20)}）→ 排列轉弱，觀望")

    # ── 招式三之①：KD 黃金交叉 ─────────────────────────────────────
    if golden and kd_k is not None and kd_k < ZHU_KD_LOW:
        score += 20
        notes.append(f"✅ KD 低檔黃金交叉（K={kd_k:.0f}/D={kd_d:.0f}，K<{ZHU_KD_LOW} 最強買點）")
    elif golden:
        score += 15
        notes.append(f"✅ KD 黃金交叉（K={kd_k:.0f}/D={kd_d:.0f}）")
    elif kd_k is not None and kd_d is not None:
        if kd_k > kd_d:
            score += 8
            notes.append(f"⚠ KD 多排列（K={kd_k:.0f}/D={kd_d:.0f}，尚未交叉）")
        elif death:
            notes.append(f"❌ KD 死亡交叉（K={kd_k:.0f}/D={kd_d:.0f}）→ 短線轉弱")
        else:
            notes.append(f"— KD 空排列（K={kd_k:.0f}/D={kd_d:.0f}）")

    # ── 招式三之②：KD 鈍化 ─────────────────────────────────────────
    kv = [x for x in ks if x is not None]
    if len(kv) >= 3:
        last3 = kv[-3:]
        if all(v > ZHU_KD_HIGH for v in last3):
            score += 8
            notes.append(f"✅ KD 高檔鈍化（連3根 K>{ZHU_KD_HIGH}：{last3[0]:.0f}/{last3[1]:.0f}/{last3[2]:.0f}）"
                         f"→ 強勢續抱，改守均線別急賣")
        elif all(v < ZHU_KD_DEEP_LOW for v in last3):
            notes.append(f"⚠ KD 低檔鈍化（連3根 K<{ZHU_KD_DEEP_LOW}）→ 弱勢別接刀，等轉折")
        else:
            notes.append(f"— KD 未鈍化（近3根 K：{last3[0]:.0f}/{last3[1]:.0f}/{last3[2]:.0f}，正常區間）")

    # ── 招式三之③：KD 背離 ─────────────────────────────────────────
    div = _kd_divergence(close_s, ks)
    if div == "bull":
        score += 10
        notes.append("✅ KD 底背離（價創新低、KD 未創低）→ 潛在買點，等紅K確認")
    elif div == "bear":
        score = max(0, score - 10)
        notes.append("⚠ KD 頂背離（價創新高、KD 未創高）→ 追高風險，留意轉折")
    else:
        notes.append(f"— KD 無明顯背離（回看 {ZHU_DIVERGE_LOOKBACK} 根價量同步）")

    # ── 趨勢與動能輔助 ──────────────────────────────────────────────
    if st_up:
        score += 15
        notes.append("✅ SuperTrend 多頭趨勢（順勢做多）")
    else:
        notes.append("❌ SuperTrend 空頭/無趨勢（逆勢，降部位）")

    if rsi_val is not None:
        if 50 <= rsi_val <= 75:
            score += 5
            notes.append(f"✅ RSI {rsi_val:.0f}（動能健康區）")
        elif rsi_val > 75:
            notes.append(f"⚠ RSI {rsi_val:.0f}（過熱，配合 KD 鈍化守均線）")
        elif rsi_val < 30:
            notes.append(f"⚠ RSI {rsi_val:.0f}（超賣，留意低檔背離反彈）")
        else:
            notes.append(f"— RSI {rsi_val:.0f}")

    # ── 停損停利提示（朱家泓：K線低點 / MA20 / −5%，硬上限 −10%）────────
    if ma20:
        notes.append(f"📌 操作：停損取 MA20({ma20:.1f}) 或 −5% 較高者（硬上限 −10%）；"
                     f"移動停利＝收盤跌破前日K低點即走")

    verdict = ("強力做多" if score >= 70 else
               "技術偏多" if score >= 50 else
               "中性觀望" if score >= 30 else "技術偏空")

    hi60 = float(close_s.tail(60).max()) if len(close_s) >= 20 else None
    sw_lo = _swing_low(close_s)
    zone = _calc_zone(close_last, ma20, ma60, None, sw_lo, hi60, "zhu") if score >= 30 else None

    teach = _teach_zhu(close_last, zone, ma10, ma20, ma60, launch, tangled,
                       days_ago, above20, kd_k, div, score)

    return {"view": "朱家泓 均線糾結+KD三招", "score": min(score, 100),
            "verdict": verdict, "notes": notes, "zone": zone, "teach": teach,
            "kd_k": kd_k, "kd_d": kd_d, "kd_golden": golden, "st": st_today,
            "tangled": tangled, "launch": launch, "kd_diverge": div}


# ── 視角 2：阿斯匹靈（籌碼：投信作帳 + 三大法人 + 乖離溫度計）──────────────
def _view_aspirin(code: str, df: pd.DataFrame, chips_rec: dict | None,
                  margin_rec: dict | None, tdcc_rec: dict | None) -> dict:
    """
    阿斯匹靈真方法論落地：
      1) 投信連買（作帳）≥3日；季底作帳窗（3/6/9/12月下半月）權重上調
      2) 三大法人同買（外資>0 且 投信>0 且 自營>0 最強）；外資連買同向
      3) 乖離溫度計（月線乖離）：燒燙別追 / 常溫可接 / 超跌承接
      4) 強勢股回檔買（收盤>MA20&MA60，回檔至 MA10 分批）
      5) 融資暴增不漲扣分；集保散戶流出加分
    """
    score = 0
    notes: list[str] = []

    # 現價與月線乖離（需 df）
    closed = _closed_series(df, 180)
    close_s = closed["close"].astype(float)
    price = float(close_s.iloc[-1])
    ma20 = float(close_s.rolling(20).mean().iloc[-1]) if len(close_s) >= 20 else None
    ma60 = float(close_s.rolling(60).mean().iloc[-1]) if len(close_s) >= 60 else None
    ma10 = float(close_s.rolling(10).mean().iloc[-1]) if len(close_s) >= 10 else None

    mon = date.today().month
    day = date.today().day
    in_window = (mon in ASP_WINDOW_MONTHS and day >= 15)   # 季底下半月作帳窗

    # ── L1：投信連買（作帳）─────────────────────────────────────────
    if chips_rec is not None:
        tc = chips_rec.get("trust_consec_days") or 0
        ts = chips_rec.get("trust_net_sum") or 0
        tn = chips_rec.get("trust_net") or 0
        base = 0
        if tc >= 7:
            base = 40
            notes.append(f"✅ 投信單獨連買 {tc} 日、近 {CHIP_DAYS} 日累買 {ts:+,} 張（強力作帳）")
        elif tc >= 5:
            base = 30
            notes.append(f"✅ 投信連買 {tc} 日、累買 {ts:+,} 張（作帳確認中）")
        elif tc >= ASP_TRUST_CONSEC:
            base = 20
            notes.append(f"⚠ 投信連買 {tc} 日（達 ≥{ASP_TRUST_CONSEC} 日門檻，觀察）")
        elif tn > 0:
            base = 5
            notes.append(f"— 投信今日買超 {tn:+,} 張（尚未連買）")
        else:
            notes.append(f"❌ 投信未連買（連買 {tc} 日，今日 {tn:+,} 張）")
        # 季底作帳窗加乘
        if base > 0 and in_window:
            bonus = round(base * 0.15)
            base += bonus
            notes.append(f"✅ 季底作帳窗（{mon}月下半月）→ 投信買盤權重上調 +{bonus}")
        elif in_window:
            notes.append(f"📌 現處季底作帳窗（{mon}月下半月），惟投信尚未進場（可留意轉買）")
        else:
            notes.append(f"— 非季底作帳窗（{mon}月），投信作帳題材淡")
        score += base

        # ── L2：三大法人同買 ────────────────────────────────────────
        fn = chips_rec.get("foreign_net") or 0
        inst = chips_rec.get("instinv_net") or 0
        dealer = inst - fn - tn   # 三大法人合計 − 外資 − 投信 = 自營商
        cd = chips_rec.get("consec_buy_days") or 0
        pos = sum(1 for x in (fn, tn, dealer) if x > 0)
        if fn > 0 and tn > 0 and dealer > 0:
            score += 20
            notes.append(f"✅ 三大法人同買（外資{fn:+,}／投信{tn:+,}／自營{dealer:+,} 張）→ 最強共識")
        elif pos == 2:
            score += 12
            notes.append(f"✅ 三大法人 2 家同買（外資{fn:+,}／投信{tn:+,}／自營{dealer:+,} 張）")
        elif fn > 0:
            score += 6
            notes.append(f"⚠ 僅外資買超 {fn:+,} 張（投信/自營未同向）")
        else:
            notes.append(f"❌ 法人未同向買超（外資{fn:+,}／投信{tn:+,}／自營{dealer:+,} 張）")
        # 外資連買同向
        if fn > 0 and cd >= 2:
            score += 10
            notes.append(f"✅ 外資+投信合計連買 {cd} 日（籌碼穩定，符合認養）")
        else:
            notes.append(f"— 外資+投信合計連買 {cd} 日（未達 ≥2 日認養門檻）")
    else:
        notes.append("— 三大法人資料不可用")

    # ── L3：乖離溫度計（月線乖離）──────────────────────────────────
    if ma20 and ma20 > 0:
        bias = (price - ma20) / ma20
        if bias > ASP_BIAS_HOT:
            notes.append(f"🔥 乖離溫度計：燒燙 +{bias*100:.1f}%（>{ASP_BIAS_HOT*100:.0f}%，別追高等回檔）")
        elif bias > ASP_BIAS_WARM:
            score += 4
            notes.append(f"🌡 乖離溫度計：溫熱 +{bias*100:.1f}%（可續抱，勿追）")
        elif bias >= ASP_BIAS_COLD:
            score += 8
            notes.append(f"🌡 乖離溫度計：常溫 {bias*100:+.1f}%（貼近月線，回檔買點區）")
        elif bias >= ASP_BIAS_DEEP:
            score += 10
            notes.append(f"❄ 乖離溫度計：偏冷 {bias*100:.1f}%（回檔至月線下，強勢股承接區）")
        else:
            score += 6
            notes.append(f"❄ 乖離溫度計：超跌 {bias*100:.1f}%（<{ASP_BIAS_DEEP*100:.0f}%，分批承接非追殺）")

    # ── L3b：強勢股回檔買（收盤 > MA20 且 > MA60）───────────────────
    if ma20 and ma60:
        if price > ma20 and price > ma60:
            score += 8
            notes.append(f"✅ 站穩 MA20/MA60（強勢股入選條件）→ 回檔至 MA10({_fmt(ma10)}) 分批進，破 MA20 砍")
        else:
            notes.append(f"— 未站穩月/季線（MA20 {ma20:.1f}／MA60 {ma60:.1f}）→ 非強勢股，暫不列阿斯匹靈池")

    # ── L4a：融資（散戶追高扣分 / 籌碼乾淨加分）──────────────────────
    if margin_rec is not None:
        mc = margin_rec.get("margin_chg") or 0
        mb = margin_rec.get("margin_balance") or 0
        c_prev = float(close_s.iloc[-2]) if len(close_s) >= 2 else price
        up_today = price >= c_prev
        if mc < 0:
            score += 12
            notes.append(f"✅ 融資減少 {mc:+,} 張（散戶退場，籌碼乾淨）")
        elif mc > 0 and not up_today:
            score = max(0, score - 6)
            notes.append(f"❌ 融資增 {mc:+,} 張但股價不漲（散戶追高套牢，扣分）")
        elif mc > 0:
            notes.append(f"⚠ 融資增加 {mc:+,} 張（餘額 {mb:,}，留意追高）")
        else:
            notes.append(f"— 融資持平（餘額 {mb:,} 張）")
    else:
        notes.append("— 融資券資料不可用")

    # ── L4b：集保散戶流出（主力吸籌）────────────────────────────────
    if tdcc_rec is not None:
        pct = tdcc_rec.get("small_chg_pct") or 0
        if tdcc_rec.get("retail_exit"):
            score += 12
            notes.append(f"✅ 集保散戶流出 {abs(pct):.1f}%（主力吸籌，週更新）")
        elif tdcc_rec.get("retail_surge"):
            score = max(0, score - 4)
            notes.append(f"⚠ 集保散戶大增 +{pct:.1f}%（過熱、主力可能倒貨）")
        else:
            notes.append(f"— 集保散戶變化 {pct:+.1f}%（週更新）")
    else:
        notes.append("— 集保戶數資料不可用")

    verdict = ("高確信做多" if score >= 75 else
               "籌碼偏多" if score >= 50 else
               "籌碼中性" if score >= 25 else "籌碼偏空")

    zone = _calc_zone(price, ma20, None, None, None, None, "asp") if score >= 25 else None

    teach = _teach_asp(price, zone, ma10, ma20, chips_rec, in_window, score)

    return {"view": "阿斯匹靈 投信作帳+法人+乖離", "score": min(score, 100),
            "verdict": verdict, "notes": notes, "zone": zone, "teach": teach}


# ── 視角 3：權證小哥（主力K棒：光頭大紅棒 + 爆量量價 + 出貨）─────────────
def _view_warrant(code: str, df: pd.DataFrame) -> dict:
    """
    權證小哥真方法論落地：
      1) 光頭大紅棒（發車）：漲≥5% + 無上影 + 實體>80% + 盤整突破
      2) 爆量：≥2×(強≥3×)；量價背離（價創高量未跟）不追
      3) 出貨訊號：高檔長上影（上影≥2×實體）/ 爆量收黑吞噬
      4) 量價轉折：爆量漲不動→偏空；爆量跌不動→偏多
      5) 停損＝跌破發車紅棒最低點；停利＝長上影/爆量吞噬/背離出
    """
    closed = _closed_series(df, 180)

    score = 0
    notes: list[str] = []
    warnings: list[str] = []

    vol = closed["volume"].astype(float)
    close = closed["close"].astype(float)
    high = closed["high"].astype(float)
    low = closed["low"].astype(float)
    openp = closed["open"].astype(float)

    c_now = float(close.iloc[-1])
    c_prev = float(close.iloc[-2]) if len(close) >= 2 else c_now
    h = float(high.iloc[-1])
    l = float(low.iloc[-1])
    o = float(openp.iloc[-1])
    hl = h - l
    body = abs(c_now - o)
    upper_shadow = h - max(c_now, o)
    lower_shadow = min(c_now, o) - l
    chg = (c_now - c_prev) / c_prev * 100 if c_prev else 0

    avg_vol = float(vol.iloc[-21:-1].mean()) if len(vol) >= 22 else None
    cur_vol = float(vol.iloc[-1])
    prev_vol = float(vol.iloc[-2]) if len(vol) >= 2 else cur_vol
    relvol = round(cur_vol / avg_vol, 2) if avg_vol and avg_vol > 0 else None

    body_ratio = (body / hl) if hl > 0 else 0     # 實體佔全K比
    up_shadow_x = (upper_shadow / body) if body > 0 else 0   # 上影/實體倍數

    # ── ① 光頭大紅棒（發車）── 每次都輸出判讀（成立/不成立皆敘述）────
    bald_red = _bald_red_k(closed)
    if bald_red:
        score += 40
        notes.append(f"⚡ 光頭大紅棒發車（漲 {chg:.1f}%、無上影、實體佔 {body_ratio*100:.0f}%、"
                     f"橫盤突破）→ 主力積極拉抬，可追")
    else:
        notes.append(f"— 非光頭大紅棒（今漲 {chg:.1f}%、實體佔 {body_ratio*100:.0f}%、"
                     f"上影 {up_shadow_x:.1f}× 實體，未達發車標準）")

    # ── ② 爆量 ── 每次都輸出量能倍數判讀 ───────────────────────────
    if relvol is not None:
        if relvol >= WAR_VOL_STRONG:
            score += 25
            notes.append(f"✅ 極強爆量 {relvol:.1f}×（≥{WAR_VOL_STRONG:.0f}×，主力積極介入）")
        elif relvol >= WAR_VOL_BASE:
            score += 18
            notes.append(f"✅ 爆量 {relvol:.1f}×（≥{WAR_VOL_BASE:.0f}×，量能確認）")
        elif relvol >= WAR_VOL_WATCH:
            score += 8
            notes.append(f"⚠ 量能 {relvol:.1f}×（略增，觀察是否續量）")
        else:
            notes.append(f"— 量能 {relvol:.1f}×（縮量，無主力積極介入）")
    else:
        notes.append("— 量能資料不足（未滿22根）")

    # ── 今日漲幅特徵 ── 每次都輸出 ─────────────────────────────────
    if chg >= 5 and not bald_red:
        score += 12
        notes.append(f"✅ 漲幅 {chg:.1f}%（強拉，但非標準光頭發車）")
    elif 2 <= chg < 5:
        score += 6
        notes.append(f"— 漲幅 {chg:.1f}%（溫和上漲）")
    elif -3 < chg < 2:
        notes.append(f"— 漲幅 {chg:.1f}%（區間波動，無主力強拉特徵）")
    else:  # chg <= -3
        warnings.append(f"⚠ 跌幅 {chg:.1f}%（賣壓湧現）")

    # ── ③ 量價關係（創高量能是否跟上）── 每次都輸出 ──────────────────
    is_new_high = len(close) >= 20 and c_now >= float(close.tail(20).max()) * 0.999
    if is_new_high and relvol is not None and relvol < 1.0:
        warnings.append(f"⚠ 量價背離（價創20日高但量僅 {relvol:.1f}×）→ 動能不足，不追")
    elif is_new_high and relvol is not None and relvol >= WAR_VOL_WATCH:
        notes.append(f"✅ 價量齊揚（創20日高且量 {relvol:.1f}×）→ 突破有量，健康")
    else:
        notes.append("— 未創20日新高（無創高量價背離疑慮）")

    # ── ④ 出貨訊號：高檔長上影 + 爆量收黑吞噬 ── 每次都輸出綜合結論 ──
    recent_high = float(close.tail(20).max()) if len(close) >= 5 else c_now
    is_near_high = c_now >= recent_high * 0.95
    long_upper = hl > 0 and body > 0 and upper_shadow >= 2 * body and is_near_high
    prev_body_hi = max(float(close.iloc[-2]), float(openp.iloc[-2])) if len(close) >= 2 else c_now
    prev_body_lo = min(float(close.iloc[-2]), float(openp.iloc[-2])) if len(close) >= 2 else c_now
    prev_red = float(close.iloc[-2]) > float(openp.iloc[-2]) if len(close) >= 2 else False
    engulf = (c_now < o and cur_vol > prev_vol and prev_red
              and o >= prev_body_hi and c_now <= prev_body_lo)
    if long_upper:
        score = max(0, score - 15)
        warnings.append(f"⚠ 高檔長上影（上影 {up_shadow_x:.1f}× 實體）→ 主力出貨警示，減碼")
    if engulf:
        score = max(0, score - 20)
        warnings.append("⚠ 爆量收黑吞噬（今量>昨量、黑K吞昨紅實體）→ 主力倒貨，出場")
    if not long_upper and not engulf:
        notes.append("✅ 無出貨型態（無高檔長上影、無爆量吞噬）")

    # ── ⑤ 量價轉折（僅爆量時判）── 每次都輸出結論 ─────────────────
    if relvol is not None and relvol >= WAR_VOL_BASE:
        is_doji = hl > 0 and body / hl < 0.25
        if (is_doji or upper_shadow >= 2 * max(body, 1e-9)) and chg < 1:
            notes.append("⚠ 爆量漲不動（長上影/十字）→ 量價轉折偏空")
        elif lower_shadow >= 2 * max(body, 1e-9) and chg < 0:
            score += 8
            notes.append("✅ 爆量跌不動（長下影/十字）→ 量價轉折偏多，低接")
        else:
            notes.append("— 爆量但無轉折K棒（順勢延續）")
    else:
        notes.append("— 未爆量，暫無量價轉折訊號")

    # ── 位階：現價落在近20日高低區間何處（追高/低接參考）──────────────
    if len(close) >= 20:
        hi20 = float(close.tail(20).max())
        lo20 = float(close.tail(20).min())
        if hi20 > lo20:
            pos_pct = (c_now - lo20) / (hi20 - lo20) * 100
            notes.append(f"📌 位階：現價位於近20日區間 {pos_pct:.0f}%（{lo20:.1f}~{hi20:.1f}）")

    # ── 集中度代理提示（分點成本無免費資料，用爆量倍數代理）─────────
    notes.append(f"📌 主力代理：以爆量倍數 {relvol if relvol else '—'}× 代替分點集中度；"
                 f"停損＝跌破發車紅棒低點({l:.1f})；停利＝出長上影/爆量吞噬/背離")

    all_notes = notes + warnings
    if not all_notes:
        all_notes = ["— 無明顯K棒主力訊號"]

    verdict = ("主力強力介入" if score >= 60 else
               "量能偏多" if score >= 35 else
               "無明顯主力" if score >= 15 else "主力缺席/出貨")

    sw_lo = _swing_low(close)
    zone = _calc_zone(c_now, None, None, None, sw_lo, None, "war") if score >= 15 else None

    teach = _teach_war(c_now, zone, bald_red, relvol, l, long_upper, engulf, score)

    return {"view": "權證小哥 光頭大紅棒+量價", "score": min(score, 100),
            "verdict": verdict, "notes": all_notes, "zone": zone, "teach": teach,
            "relvol": relvol, "bald_red_k": bald_red}


# ── 視角 4：張捷（基本強勢：RS 相對強弱 + 位階 + 法人認養）────────────────
def _view_zhang(code: str, df: pd.DataFrame, industry: str = "未分類") -> dict:
    """
    張捷真方法論落地：
      1) RS 相對強弱（核心）：個股 20 日報酬 vs 大盤(0050) 20 日報酬 → RS>1 強
      2) 位階：距年線合理區（剛突破年線佳、>+50% 過熱）；創新高
      3) 站上季線 + 帶量 + 均量遞增
      4) 法人認養：外資/投信連買
      5) ADX 趨勢強度（產業還在成長的量化替代）
    """
    closed = _closed_series(df, 280)

    score = 0
    notes: list[str] = []
    rs_excess = None   # 供手把手教學引用（個股超額報酬 vs 大盤）

    close_s = closed["close"].astype(float)
    high_s = closed["high"].astype(float)
    vol_s = closed["volume"].astype(float)
    price = float(close_s.iloc[-1])
    ma60 = float(close_s.rolling(60).mean().iloc[-1]) if len(close_s) >= 60 else None
    ma20_val = float(close_s.rolling(20).mean().iloc[-1]) if len(close_s) >= 20 else None
    yearline = float(close_s.rolling(240).mean().iloc[-1]) if len(close_s) >= 240 else None

    # ── ① RS 相對強弱（核心）──────────────────────────────────────
    if len(close_s) > ZHANG_RS_LOOKBACK:
        base = float(close_s.iloc[-1 - ZHANG_RS_LOOKBACK])
        stock_ret = (price - base) / base * 100 if base > 0 else 0
        mkt_ret = _market_ret(ZHANG_RS_LOOKBACK)
        if mkt_ret is not None:
            excess = stock_ret - mkt_ret
            rs_excess = excess
            if excess >= ZHANG_RS_STRONG:
                score += 30
                notes.append(f"✅ RS 強勢：近{ZHANG_RS_LOOKBACK}日 {stock_ret:+.1f}% 大幅勝大盤 "
                             f"{mkt_ret:+.1f}%（超額 {excess:+.1f}%）→ 張捷核心：買最強的")
            elif excess > 0:
                score += 18
                notes.append(f"✅ RS>1：近{ZHANG_RS_LOOKBACK}日 {stock_ret:+.1f}% 勝大盤 "
                             f"{mkt_ret:+.1f}%（超額 {excess:+.1f}%）")
            else:
                notes.append(f"❌ RS<1：近{ZHANG_RS_LOOKBACK}日 {stock_ret:+.1f}% 弱於大盤 "
                             f"{mkt_ret:+.1f}%（張捷：弱勢不追）")
        else:
            # 大盤資料缺 → 退回絕對動能
            if stock_ret >= ZHANG_RS_STRONG:
                score += 18
                notes.append(f"✅ 近{ZHANG_RS_LOOKBACK}日 {stock_ret:+.1f}%（絕對強勢；大盤資料缺，RS 略）")
            else:
                notes.append(f"— 近{ZHANG_RS_LOOKBACK}日 {stock_ret:+.1f}%（大盤資料缺，RS 未計）")

    # ── ② 站上季線 + 帶量 ──────────────────────────────────────────
    if ma60 is not None:
        avg20v = float(vol_s.iloc[-21:-1].mean()) if len(vol_s) >= 22 else None
        vmult = (float(vol_s.iloc[-1]) / avg20v) if (avg20v and avg20v > 0) else None
        vma5 = float(vol_s.rolling(5).mean().iloc[-1]) if len(vol_s) >= 5 else None
        vma20 = float(vol_s.rolling(20).mean().iloc[-1]) if len(vol_s) >= 20 else None
        if price > ma60:
            if vmult is not None and vmult >= ZHANG_VOL_MULT:
                score += 15
                notes.append(f"✅ 站上季線 MA60({ma60:.1f}) 且帶量 {vmult:.1f}×（有量才是真突破）")
            else:
                score += 8
                notes.append(f"✅ 站上季線 MA60({ma60:.1f})（惟量能 {_fmt(vmult)}× 偏弱）")
            if vma5 and vma20 and vma5 > vma20:
                score += 5
                notes.append("✅ 均量遞增（MA5量>MA20量）→ 資金持續流入")
            elif vma5 and vma20:
                notes.append("— 均量遞減（MA5量<MA20量）→ 追價量能轉弱，留意")
        else:
            notes.append(f"❌ 跌破季線 MA60({ma60:.1f})（張捷：結構轉弱，列刪去）")

    # ── ③ 位階：距年線 ─────────────────────────────────────────────
    if yearline is not None and yearline > 0:
        dist = (price - yearline) / yearline
        if 0 < dist <= ZHANG_YEARLINE_HOT:
            score += 15
            notes.append(f"✅ 站上年線 +{dist*100:.0f}%（初升~主升段合理位階，續抱空間大）")
        elif dist > ZHANG_YEARLINE_HOT:
            score += 5
            notes.append(f"⚠ 距年線 +{dist*100:.0f}%（>{ZHANG_YEARLINE_HOT*100:.0f}% 位階偏高，追高風險）")
        else:
            notes.append(f"❌ 跌破年線 {dist*100:.0f}%（張捷「月盈則虧」→ 列入刪去）")
    else:
        notes.append("— 資料不足 240 根，年線位階未計（新上市或快取回補中）")

    # ── ④ 創新高（比重季季上升的量化替代）──────────────────────────
    if len(high_s) >= 60:
        hi60 = float(high_s.tail(60).max())
        if price >= hi60 * 0.999:
            score += 10
            notes.append("✅ 逼近/創 60 日新高（強勢創高，主流領漲特徵）")
        else:
            gap = (hi60 - price) / price * 100
            notes.append(f"— 距 60 日高點尚差 {gap:.1f}%")

    # ── ⑤ ADX 趨勢強度 ─────────────────────────────────────────────
    adx = _scan._adx_last(closed)
    if adx is not None:
        if adx >= 35:
            score += 12
            notes.append(f"✅ ADX={adx:.0f}：趨勢強勁（產業正向加速）")
        elif adx >= 25:
            score += 8
            notes.append(f"✅ ADX={adx:.0f}：有趨勢（符合「產業還在成長」）")
        elif adx >= 15:
            score += 3
            notes.append(f"⚠ ADX={adx:.0f}：弱趨勢（觀察是否加速）")
        else:
            notes.append(f"❌ ADX={adx:.0f}：無趨勢（張捷：刪去候選）")

    # ── 人工補充（張捷法基本面需人工）+ 停損提示 ─────────────────────
    notes.append(f"📌 人工補充（張捷）：確認 {industry} 產業比重 ≥50%、毛利率 ≥30%、白牌/大客戶認證")
    notes.append(f"📌 操作：結構停損＝跌破季線 MA60({_fmt(ma60)}) 或波段起漲低點；"
                 f"續抱＝大盤未破月線 + RS>1")

    verdict = ("產業強勢領漲" if score >= 75 else
               "產業趨勢向上" if score >= 50 else
               "中性/觀察" if score >= 25 else "產業趨勢不明")

    hi60_val = float(close_s.tail(60).max()) if len(close_s) >= 20 else None
    zone = _calc_zone(price, ma20_val, None, yearline, None, hi60_val, "zhang") if score >= 25 else None

    teach = _teach_zhang(price, zone, ma60, yearline, rs_excess, score)

    return {"view": "張捷 相對強弱+位階", "score": min(score, 100),
            "verdict": verdict, "notes": notes, "zone": zone, "teach": teach,
            "industry": industry}


# ── 綜合操作策略（整合四師 → 買在哪/賣在哪/怎麼操作）──────────────────────
def _build_strategy(price: float, closed: pd.DataFrame, views: dict,
                    confirm: int, total: float) -> dict | None:
    """把四師分析整合成一個可執行的操作區塊，供前端「怎麼操作」卡片直接用。"""
    if price <= 0:
        return None
    close_s = closed["close"].astype(float)
    ma10 = float(close_s.rolling(10).mean().iloc[-1]) if len(close_s) >= 10 else None
    ma20 = float(close_s.rolling(20).mean().iloc[-1]) if len(close_s) >= 20 else None
    ma60 = float(close_s.rolling(60).mean().iloc[-1]) if len(close_s) >= 60 else None
    yline = float(close_s.rolling(240).mean().iloc[-1]) if len(close_s) >= 240 else None
    hi60 = float(close_s.tail(60).max()) if len(close_s) >= 20 else None
    sw_lo = _swing_low(close_s)

    zones = [v.get("zone") for v in views.values() if v.get("zone")]

    # 進場區：彙整四師進場區（取共識包絡）；無 zone 時退回 MA10~現價
    if zones:
        buy_lo = round(min(z["entry_lo"] for z in zones), 2)
        buy_hi = round(max(z["entry_hi"] for z in zones), 2)
    else:
        buy_lo = round((ma10 or ma20 or price) * 0.98, 2)
        buy_hi = round(price * 1.01, 2)
    if buy_lo >= buy_hi:
        buy_lo = round(buy_hi * 0.98, 2)

    # 結構停損：取四師較保守（最高停損價）→ 較早止血；再夾在 [-10%, -3%] 間
    if zones:
        stop = round(max(z["stop"] for z in zones), 2)
    else:
        stop = round((sw_lo or price * 0.93), 2)
    stop = max(stop, round(price * 0.90, 2))   # 硬地板 −10%
    stop = min(stop, round(price * 0.97, 2))   # 至少留 3% 呼吸空間

    # 目標：前高突破 + R 倍數滿足點
    entry_mid = (buy_lo + buy_hi) / 2
    risk = entry_mid - stop
    target1 = round(hi60 * 1.02, 2) if (hi60 and hi60 > price) else round(price * 1.10, 2)
    target2 = round(entry_mid + risk * 3, 2) if risk > 0 else round(price * 1.20, 2)
    target2 = max(target2, round(target1 * 1.06, 2))
    rr = round((target1 - entry_mid) / risk, 1) if risk > 0 else 0

    # 建議部位：依過關維度數 + 總分（0~40%）
    if total < 40:
        position_pct = 0
    else:
        position_pct = min(confirm * 10 + (10 if total >= 70 else 0), 40)

    # 情境文字
    dim_names = [k for k, v in views.items() if v["score"] >= 50]
    passed = "、".join(dim_names) if dim_names else "無"
    if position_pct == 0:
        scenario = (f"四維共振不足（過關：{passed}），不宜進場。等技術起漲＋籌碼轉多再評估。")
    else:
        scenario = (
            f"過關 {confirm}/4 維（{passed}）。分批進場區 {buy_lo}~{buy_hi}："
            f"回踩 MA10({_fmt(ma10)}) 佈 1/2、突破 {target1} 追 1/2；"
            f"跌破 {stop} 出場（結構停損）；第一目標 {target1}（前高）、"
            f"第二目標 {target2}（3R 滿足點）。建議部位 {position_pct}%（風報比 {rr}）。"
        )
    return {
        "buy_zone_lo": buy_lo, "buy_zone_hi": buy_hi,
        "stop": stop, "target1": target1, "target2": target2,
        "rr": rr, "position_pct": position_pct, "scenario": scenario,
    }


# ── 綜合分析 ───────────────────────────────────────────────────────────────
WEIGHTS = {"朱家泓": 0.25, "阿斯匹靈": 0.35, "權證小哥": 0.20, "張捷": 0.20}


def _lookup_meta(code: str) -> tuple[str, str]:
    """從精選宇宙查 (name, industry)；找不到回 (code, '')。"""
    try:
        from universe import all_codes, load_full_universe
        for c, n, ind in all_codes():
            if c == code:
                return n, ind
        for c, n, ind in load_full_universe():
            if c == code:
                return n, ind
    except Exception:
        pass
    return code, ""


def analyze_one(code: str, name: str = "", industry: str = "") -> dict | None:
    """對單一股票跑四維度分析，回傳完整結果字典（平行下載，速度提升 3x）。
    code 支援代號(2330)或中文名(盟立)：非純數字時用 query._resolve_code 轉代號。"""
    code = (code or "").strip()
    if code and not code.isdigit():
        try:
            import query
            resolved = query._resolve_code(code)
            if resolved:
                code = resolved
        except Exception:
            pass

    if not name or not industry:
        auto_name, auto_ind = _lookup_meta(code)
        name = name or auto_name
        industry = industry or auto_ind

    # 四路平行下載：ohlcv / 籌碼 / 融資 / 集保
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_df     = ex.submit(_load_ohlcv,      code)
        f_chips  = ex.submit(_load_chips_rec,  code)
        f_margin = ex.submit(_load_margin_rec, code)
        f_tdcc   = ex.submit(_load_tdcc_rec,   code)
        df         = f_df.result()
        chips_rec  = f_chips.result()
        margin_rec = f_margin.result()
        tdcc_rec   = f_tdcc.result()

    if df is None:
        return None

    v1 = _view_zhujiahe(code, df)
    v2 = _view_aspirin(code, df, chips_rec, margin_rec, tdcc_rec)
    v3 = _view_warrant(code, df)
    v4 = _view_zhang(code, df, industry or "未分類")

    # 加權總分
    total = round(
        v1["score"] * WEIGHTS["朱家泓"] +
        v2["score"] * WEIGHTS["阿斯匹靈"] +
        v3["score"] * WEIGHTS["權證小哥"] +
        v4["score"] * WEIGHTS["張捷"], 1
    )

    # 多維度共振計數（≥50分才算該維度確認）
    views = {"朱家泓": v1, "阿斯匹靈": v2, "權證小哥": v3, "張捷": v4}
    confirm = sum(1 for v in views.values() if v["score"] >= 50)

    # 綜合操作策略（買賣點/部位/情境）
    closed_all = _closed_series(df, 280)
    price_now = float(closed_all["close"].astype(float).iloc[-1])
    strategy = _build_strategy(price_now, closed_all, views, confirm, total)

    # 綜合手把手總教學（今天到底怎麼操作，白話 3~6 步）
    playbook = _build_playbook(price_now, closed_all, views, strategy, confirm, total)

    # 綜合建議
    if total >= 70 and confirm >= 3:
        recommendation = "🟢 強力做多候選（三維以上共振）"
    elif total >= 55 and confirm >= 2:
        recommendation = "🟡 做多候選（兩維共振，量能確認後進場）"
    elif total >= 40:
        recommendation = "⚪ 觀察名單（條件未齊，等突破確認）"
    else:
        recommendation = "🔴 不宜進場（多維度偏空）"

    # 頂層 price/chg(前端詳情面板契約要)：用已收盤序列的最新價與前一日算漲跌幅
    _c = closed_all["close"].astype(float)
    _chg = round((price_now - float(_c.iloc[-2])) / float(_c.iloc[-2]) * 100, 2) if len(_c) >= 2 and float(_c.iloc[-2]) else 0.0
    return {
        "ok": True,
        "code": code, "name": name or code, "industry": industry,
        "date": str(date.today()), "total_score": total,
        "confirm_dims": confirm,
        "recommendation": recommendation,
        "price": round(price_now, 2), "chg": _chg,
        "strategy": strategy,
        "playbook": playbook,
        "views": views,
    }


# ── 報告輸出 ────────────────────────────────────────────────────────────────
def _fmt(x: float | None, nd: int = 1) -> str:
    """數字→字串，None 顯示 —。避免在 f-string 的格式規格內做條件式（語法非法）。"""
    return f"{x:.{nd}f}" if x is not None else "—"


def _bar(score: int, width: int = 20) -> str:
    filled = round(score / 100 * width)
    return "█" * filled + "░" * (width - filled)


def print_report(result: dict) -> None:
    code = result["code"]
    name = result["name"]
    total = result["total_score"]
    confirm = result["confirm_dims"]
    rec = result["recommendation"]

    SEP = "═" * 58
    print(f"\n{SEP}")
    print(f"  {code} {name}  金融分析團隊報告")
    print(f"  {result['date']}  產業：{result['industry'] or '未分類'}")
    print(SEP)
    print(f"\n  總分 {total:.0f}/100  {_bar(int(total))}  {confirm}/4 維度共振")
    print(f"  {rec}\n")
    print("─" * 58)

    for key, v in result["views"].items():
        sc = v["score"]
        print(f"\n【{v['view']}】  {sc}/100  {_bar(sc, 15)}")
        print(f"  → {v['verdict']}")
        for note in v["notes"]:
            print(f"    {note}")
        teach = v.get("teach")
        if teach:
            print("  ── 手把手教學 ──")
            for t in teach:
                pr = f"  [{t['price']}]" if t.get("price") else ""
                print(f"    ▸ {t['step']}：{t['action']}{pr}")
                print(f"        理由：{t['reason']}")

    stg = result.get("strategy")
    if stg:
        print(f"\n{'─' * 58}")
        print("【綜合操作策略】")
        print(f"  進場區 {stg['buy_zone_lo']} ~ {stg['buy_zone_hi']}   "
              f"停損 {stg['stop']}   風報比 {stg['rr']}")
        print(f"  目標 T1 {stg['target1']} / T2 {stg['target2']}   "
              f"建議部位 {stg['position_pct']}%")
        print(f"  情境：{stg['scenario']}")

    pb = result.get("playbook")
    if pb:
        print(f"\n{'─' * 58}")
        print("【今天到底怎麼操作 · 總教學 playbook】")
        for s in pb:
            pr = f"  [{s['price']}]" if s.get("price") else ""
            print(f"  {s['step']}：{s['action']}{pr}")
            print(f"      理由：{s['reason']}")

    print(f"\n{SEP}\n")


def to_telegram(result: dict) -> str:
    code = result["code"]
    name = result["name"]
    total = result["total_score"]
    confirm = result["confirm_dims"]
    rec = result["recommendation"]
    lines = [
        f"📊 金融分析團隊｜{code} {name}",
        f"總分 {total:.0f}/100  {confirm}/4 維度共振",
        f"{rec}",
        "━━━━━━━━━━━━",
    ]
    for key, v in result["views"].items():
        sc = v["score"]
        lines.append(f"【{v['view'][:5]}】{sc}分 → {v['verdict']}")
        # 只放最重要的前2條
        for note in v["notes"][:2]:
            lines.append(f"  {note}")
    stg = result.get("strategy")
    if stg:
        lines.append("━━━━━━━━━━━━")
        lines.append(f"進場 {stg['buy_zone_lo']}~{stg['buy_zone_hi']}｜停損 {stg['stop']}"
                     f"｜T1 {stg['target1']}｜部位 {stg['position_pct']}%")
    lines.append("━━━━━━━━━━━━")
    lines.append("量化阿森 · 金融分析團隊")
    return "\n".join(lines)


# ── 掃描模式（全宇宙四維度共振候選）──────────────────────────────────────
def scan_universe(top_n: int = 10) -> list[dict]:
    """掃精選宇宙，取出四維度總分最高的前 N 支，只顯示做多候選。"""
    from universe import all_codes
    rows = all_codes()
    # 先用 scan.py 批次載資料
    data = _scan.load_universe_data(rows, use_cache_only=True)
    chip_map = _chips.load_chips([c for c, _, _ in rows], days=CHIP_DAYS, offline=True) if _chips else {}
    margin_map = _margin_mod.load_margin([c for c, _, _ in rows], offline=True) if _margin_mod else {}
    tdcc_map = _tdcc_mod.load_tdcc([c for c, _, _ in rows], offline=True) if _tdcc_mod else {}

    results: list[dict] = []
    for code, name, ind in rows:
        df = data.get(code)
        if df is None or len(df) < 30:
            continue
        v1 = _view_zhujiahe(code, df)
        v2 = _view_aspirin(code, df, chip_map.get(code), margin_map.get(code), tdcc_map.get(code))
        v3 = _view_warrant(code, df)
        v4 = _view_zhang(code, df, ind)
        total = round(
            v1["score"] * 0.25 + v2["score"] * 0.35 +
            v3["score"] * 0.20 + v4["score"] * 0.20, 1
        )
        confirm = sum(1 for v in [v1, v2, v3, v4] if v["score"] >= 50)
        results.append({
            "code": code, "name": name, "industry": ind,
            "total_score": total, "confirm_dims": confirm,
            "scores": {k: v["score"] for k, v in
                       zip(["朱家泓", "阿斯匹靈", "權證小哥", "張捷"], [v1, v2, v3, v4])},
        })

    results.sort(key=lambda r: (r["confirm_dims"], r["total_score"]), reverse=True)
    return results[:top_n]


# ── CLI ──────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description="金融分析團隊四維度分析器")
    ap.add_argument("codes", nargs="*", help="股票代號，如 2330 6669")
    ap.add_argument("--scan", action="store_true", help="掃精選宇宙四維度共振候選股")
    ap.add_argument("--top", type=int, default=10, help="--scan 顯示前N名（預設10）")
    ap.add_argument("--push", action="store_true", help="分析後推 Telegram")
    ap.add_argument("--json", action="store_true", help="輸出 JSON（供程式串接）")
    args = ap.parse_args()

    if args.scan:
        print("[analyst] 掃描精選宇宙中，只用本地快取…")
        top = scan_universe(top_n=args.top)
        SEP = "─" * 58
        print(f"\n{'═'*58}")
        print(f"  金融分析團隊｜四維度共振候選股  TOP {args.top}")
        print(f"{'═'*58}")
        for r in top:
            sc = r["scores"]
            print(f"  {r['code']} {r['name']:10s} "
                  f"總分{r['total_score']:4.0f}  {r['confirm_dims']}/4維 "
                  f"朱{sc['朱家泓']:3.0f} 阿{sc['阿斯匹靈']:3.0f} "
                  f"權{sc['權證小哥']:3.0f} 張{sc['張捷']:3.0f}  {r['industry']}")
        print(f"{'═'*58}\n")
        return

    if not args.codes:
        ap.print_help()
        return

    all_results = []
    for code in args.codes:
        print(f"[analyst] 分析 {code} 中…")
        res = analyze_one(code)
        if res is None:
            print(f"[analyst] {code} 無法取得資料，略過")
            continue
        all_results.append(res)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print_report(res)
        if args.push and _broadcast is not None:
            msg = to_telegram(res)
            try:
                _broadcast(msg, title=f"金融分析｜{code} {res['name']}", priority="default")
                print(f"[analyst] Telegram 已推送 {code}")
            except Exception as e:
                print(f"[analyst] 推送失敗：{e}")

    if len(all_results) > 1 and not args.json:
        print(f"\n{'═'*58}")
        print("  綜合比較")
        print(f"{'═'*58}")
        for r in sorted(all_results, key=lambda x: x["total_score"], reverse=True):
            print(f"  {r['code']} {r['name']:10s}  "
                  f"總分 {r['total_score']:4.1f}  {r['confirm_dims']}/4維  {r['recommendation'][:12]}")
        print(f"{'═'*58}\n")


if __name__ == "__main__":
    main()
