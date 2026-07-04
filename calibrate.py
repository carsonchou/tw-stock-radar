# -*- coding: utf-8 -*-
"""
calibrate.py — 數據獵手「輕量校準」(Track B 輔助，驗證用)

對齊 scan._analyse_core 的『單一 SuperTrend 翻多』做多訊號，自寫一支輕量事件回測，
在 train/test(代碼末位 偶/奇 切分) 上：
  1. 小網格掃 VOL_MULT / ST_MULT / CHAND_MULT；
  2. 量化『選股池濾網(趨勢佔比+流動性)』前後對比；
  3. 找 train 最佳，到 test(OOS) 驗證是否穩健、非過擬合；
  4. 只把『明顯更好且 OOS 穩健』的參數回寫 scan.py 常數(預設保守不寫)。

刻意不用 strategy.backtest_long_only(那是 Triple SuperTrend + Regime，與本訊號不同源，
校出來不適用)。指標序列直接引用 validate.py 的向量化版本與 scan.py 常數(單一真相源、不看未來)：
  進場：ST 翻多(已收盤那根)+ RSI 30~70 + 量能 relVol≥VOL_MULT + 排除漲跌停(no_chase)。
  出場：CHAND_MULT×ATR22 停損 / +TP1_R×R 停利 / 滿 15 根 time-stop(以收盤結算)。
  成本：tw_real 0.585% round-trip。前瞻報酬 shift(-N) 不偷看當根。

用法：
  python calibrate.py                 # 精選宇宙、讀快取、不回寫(只報告)
  python calibrate.py --full          # 全市場(慢)
  python calibrate.py --apply         # 允許把『明顯更好且OOS穩健』參數回寫 scan.py
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import scan                                          # 常數 + _read_cache(單一真相源)
import validate as V                                 # 向量化 _st_dir_series / _rsi_series
from universe import all_codes, load_full_universe

OUT_MD = scan.ROOT / "twdata" / "calibrate_result.md"

# 出場/評估常數(對齊 track.py)
TIME_STOP_BARS = 15                                  # 別抄 strategy 的 45
FWD_HORIZON = 10                                     # 前瞻報酬視窗(根)
COST_RT = 0.585                                      # tw_real round-trip 成本(%)
MIN_TRADES = 25                                      # 樣本太少 → fitness 視為無效
MIN_BARS = 180

# 小網格(別過度調參)：以 scan 現值為中心各掃 ±1 檔
ST_MULT_GRID = [2.5, 3.0, 3.5]                       # scan 現值 3.0
VOL_MULT_GRID = [1.3, 1.5, 1.8]                      # scan 現值 1.5
CHAND_MULT_GRID = [3.0, 3.5, 4.0]                    # scan 現值 3.5(台股回測背書)
TF_MIN_GRID = [0.10, 0.15, 0.25]                     # 池濾網 趨勢佔比門檻(scan 現值 0.15)
TURNOVER_MIN = scan.POOL_TURNOVER_MIN                # 流動性門檻沿用 scan(2000萬)


# ── 載入快取(小寫欄位) ──────────────────────────────────────────────────────
def load_stocks(rows) -> list[tuple[str, pd.DataFrame]]:
    out = []
    for code, _, _ in rows:
        df = scan._read_cache(code)
        if df is None or len(df) < MIN_BARS:
            continue
        d = df.rename(columns={c: c.lower() for c in df.columns}).reset_index(drop=True)
        out.append((code, d))
    return out


# ── 每檔不隨 swept 參數變的序列(算一次) ─────────────────────────────────────
def _atr_series(d: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = d["high"].astype(float), d["low"].astype(float), d["close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


class Pre:
    """每檔預算結果(不含 st_mult/chand_mult 依賴項)。"""
    __slots__ = ("code", "d", "close", "high", "low", "rsi", "atr", "relvol", "ret",
                 "tf_tr", "to_tr", "n")

    def __init__(self, code, d):
        self.code = code
        self.d = d
        self.close = d["close"].astype(float).to_numpy()
        self.high = d["high"].astype(float).to_numpy()
        self.low = d["low"].astype(float).to_numpy()
        self.rsi = V._rsi_series(d["close"].astype(float), scan.RSI_PERIOD).to_numpy()
        self.atr = _atr_series(d, scan.CHAND_LEN).to_numpy()
        vol = d["volume"].astype(float)
        self.relvol = (vol / vol.rolling(20).mean().shift(1)).to_numpy()
        self.ret = d["close"].astype(float).pct_change().to_numpy() * 100.0
        # ── 池濾網特徵改 trailing(只用截至當根 i 的過去資料、不看未來，可交易 OOS) ──
        #   趨勢佔比 = 近 POOL_LOOKBACK 根 ADX≥thr 的『滾動』比例；流動性 = 近60根 收盤×量 滾動中位。
        #   每根都有一個值 → 進場那根 i 取 tf_tr[i]/to_tr[i] 做 per-trade gate(非全期靜態選股)。
        adx = V._adx_series(d, scan.ADX_PERIOD)
        adx_ge = (adx >= scan.ADX_TREND_THR).astype(float)
        self.tf_tr = adx_ge.rolling(scan.POOL_LOOKBACK, min_periods=20).mean().to_numpy()
        dv = d["close"].astype(float) * vol
        self.to_tr = dv.rolling(60, min_periods=20).median().to_numpy()
        self.n = len(self.close)


def precompute(stocks) -> list[Pre]:
    return [Pre(c, d) for c, d in stocks]


# ── ST 方向序列快取(只隨 st_mult 變) ────────────────────────────────────────
_ST_CACHE: dict[tuple[str, float], np.ndarray] = {}


def _st_dir(p: Pre, st_mult: float) -> np.ndarray:
    key = (p.code, st_mult)
    s = _ST_CACHE.get(key)
    if s is None:
        s = V._st_dir_series(p.d, scan.ST_PERIOD, st_mult)
        _ST_CACHE[key] = s
    return s


# ── 單檔事件回測 → trade 列表 ───────────────────────────────────────────────
def simulate(p: Pre, st_mult: float, vol_mult: float, chand_mult: float) -> list[dict]:
    st = _st_dir(p, st_mult)
    close, rsi, atr, relvol, ret = p.close, p.rsi, p.atr, p.relvol, p.ret
    n = p.n
    start = max(scan.ST_PERIOD + 1, scan.CHAND_LEN, scan.RSI_PERIOD, 21)
    trades = []
    for i in range(start, n - 1):
        # 進場條件：ST 翻多(已收盤那根 i) + RSI 健康 + 量能達標 + 非漲跌停
        if not (st[i - 1] == -1 and st[i] == 1):
            continue
        ri = rsi[i]
        if not (np.isfinite(ri) and 30 <= ri <= 70):
            continue
        rv = relvol[i]
        if not (np.isfinite(rv) and rv >= vol_mult):
            continue
        if np.isfinite(ret[i]) and abs(ret[i]) >= scan.LIMIT_PCT:   # 漲跌停不可追
            continue
        a = atr[i]
        if not (np.isfinite(a) and a > 0):
            continue
        entry = close[i]
        R = chand_mult * a
        stop = entry - R
        tp1 = entry + scan.TP1_R * R
        # 前瞻報酬(固定視窗、不偷看當根 → 從 i 起算到 i+H)
        fwd = (close[i + FWD_HORIZON] / entry - 1.0) * 100.0 - COST_RT \
            if i + FWD_HORIZON < n else np.nan
        # 逐根走出場(隔日 i+1 起)
        exit_reason, exit_px = "open", close[-1]
        for j in range(i + 1, n):
            lo, hi, cl = p.low[j], p.high[j], close[j]
            if lo <= stop:                       # 保守：同根先觸停損
                exit_reason, exit_px = "stop", stop
                break
            if hi >= tp1:
                exit_reason, exit_px = "tp1", tp1
                break
            if j - i >= TIME_STOP_BARS:          # time-stop 以收盤結算
                exit_reason, exit_px = "time", cl
                break
        if exit_reason == "open":
            continue                              # 未走完評估期 → 不計入(避免半截樣本)
        gross = (exit_px - entry) / entry * 100.0
        net = gross - COST_RT
        r_mult = (exit_px - entry) / R
        trades.append({"code": p.code, "ret_pct": net, "r": r_mult,
                       "exit_reason": exit_reason, "win": net > 0,
                       "tp1": exit_reason == "tp1", "fwd": fwd,
                       # 進場那根 i 的 trailing 池特徵(per-trade gate 用，不看未來)
                       "tf": p.tf_tr[i], "to": p.to_tr[i]})
    return trades


def _trade_pool_pass(t: dict, tf_min: float) -> bool:
    """單筆交易在進場那根是否過 trailing 池濾網(趨勢佔比+流動性)。NaN→不過(保守)。"""
    tf, to = t["tf"], t["to"]
    return (np.isfinite(tf) and tf >= tf_min and np.isfinite(to) and to >= TURNOVER_MIN)


# ── 聚合指標 + fitness ──────────────────────────────────────────────────────
def metrics(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return dict(n=0, n_stocks=0, prof_stock_ratio=0.0, fwd_med=0.0,
                    tp_hit=0.0, win_rate=0.0, med_r=0.0, fitness=-99.0)
    nets = np.array([t["ret_pct"] for t in trades])
    rs = np.array([t["r"] for t in trades])
    fwds = np.array([t["fwd"] for t in trades if np.isfinite(t["fwd"])])
    tp_hit = float(np.mean([t["tp1"] for t in trades]))
    win_rate = float(np.mean(nets > 0))
    fwd_med = float(np.median(fwds)) if len(fwds) else 0.0
    med_r = float(np.median(rs))
    # 獲利檔比例：每檔淨報酬加總 > 0 的比例
    by_stock: dict[str, float] = {}
    for t in trades:
        by_stock[t["code"]] = by_stock.get(t["code"], 0.0) + t["ret_pct"]
    prof_stock_ratio = float(np.mean([v > 0 for v in by_stock.values()])) if by_stock else 0.0
    # fitness = 前瞻報酬中位 + 命中停利比 + ret-over-risk(中位R)；樣本太少淘汰
    fitness = (0.5 * fwd_med + 3.0 * tp_hit + 0.4 * med_r) if n >= MIN_TRADES else -99.0
    return dict(n=n, n_stocks=len(by_stock), prof_stock_ratio=round(prof_stock_ratio, 3),
                fwd_med=round(fwd_med, 3), tp_hit=round(tp_hit, 3),
                win_rate=round(win_rate, 3), med_r=round(med_r, 3),
                fitness=round(fitness, 4))


def collect(pres: list[Pre], st_mult, vol_mult, chand_mult,
            pool_only=False, tf_min=0.0) -> list[dict]:
    out = []
    for p in pres:
        for t in simulate(p, st_mult, vol_mult, chand_mult):
            if pool_only and not _trade_pool_pass(t, tf_min):   # per-trade trailing gate
                continue
            out.append(t)
    return out


# ── train/test 切分(代碼末位 偶=train / 奇=test，抄 tw_optimize.split_train_test) ──
def split(pres: list[Pre]) -> tuple[list[Pre], list[Pre]]:
    train, test = [], []
    for p in pres:
        c = p.code
        if not c[-1].isdigit():
            continue
        (train if int(c[-1]) % 2 == 0 else test).append(p)
    return train, test


# ── 網格搜尋(train) ─────────────────────────────────────────────────────────
def grid_search(train: list[Pre]) -> tuple[dict, dict, list[dict]]:
    best_params, best_m, best_fit = None, None, -1e9
    traj = []
    for sm in ST_MULT_GRID:
        for vm in VOL_MULT_GRID:
            for cm in CHAND_MULT_GRID:
                base_trades = collect(train, sm, vm, cm)         # 不濾(全宇宙)
                for tf in TF_MIN_GRID:
                    pool_trades = [t for t in base_trades if _trade_pool_pass(t, tf)]
                    m = metrics(pool_trades)
                    rec = dict(st_mult=sm, vol_mult=vm, chand_mult=cm, tf_min=tf,
                               **{k: m[k] for k in ("n", "fitness", "fwd_med",
                                                    "tp_hit", "med_r", "prof_stock_ratio")})
                    traj.append(rec)
                    if m["fitness"] > best_fit:
                        best_fit, best_params, best_m = m["fitness"], dict(
                            st_mult=sm, vol_mult=vm, chand_mult=cm, tf_min=tf), m
    return best_params, best_m, traj


# ── 池濾網前後對比(某組參數)：trade-level trailing gate ──────────────────────
def pool_compare(pres: list[Pre], params: dict) -> tuple[dict, dict, int, int]:
    """回傳 (全宇宙metrics, 濾後池metrics, 有交易檔數, 濾後池涉及檔數)。
    池濾網是 per-trade(進場那根 trailing)，故『檔數』指『有貢獻交易的檔』。"""
    sm, vm, cm, tf = params["st_mult"], params["vol_mult"], params["chand_mult"], params["tf_min"]
    all_trades = collect(pres, sm, vm, cm, pool_only=False)
    pool_trades = [t for t in all_trades if _trade_pool_pass(t, tf)]
    n_stocks_all = len({t["code"] for t in all_trades})
    n_stocks_pool = len({t["code"] for t in pool_trades})
    return metrics(all_trades), metrics(pool_trades), n_stocks_all, n_stocks_pool


# ── 報告 + 回寫 ─────────────────────────────────────────────────────────────
def _fmt_m(m: dict) -> str:
    return (f"n={m['n']:<4} 獲利檔比={m['prof_stock_ratio']*100:.0f}% "
            f"前瞻中位={m['fwd_med']:+.2f}% 命中停利={m['tp_hit']*100:.0f}% "
            f"中位R={m['med_r']:+.2f} 勝率={m['win_rate']*100:.0f}%")


def write_report(best_params, train_all, train_pool, test_all, test_pool,
                 n_tr, n_te, np_tr, np_te, traj, defaults, applied: str,
                 scope: str) -> None:
    d_prof = (test_pool['prof_stock_ratio'] - test_all['prof_stock_ratio']) * 100
    d_fwd = test_pool['fwd_med'] - test_all['fwd_med']
    if d_prof > 1 or d_fwd > 0.05:
        verdict = "池濾網在此宇宙『有正貢獻』(OOS 獲利檔比/前瞻中位變好)"
    elif d_prof >= -1 and d_fwd >= -0.05:
        verdict = "池濾網在此宇宙『幾乎不咬/中性』(濾掉的不見得更差)"
    else:
        verdict = ("池濾網在此宇宙『trailing 後反而略降』——"
                   "對這個單一 SuperTrend 訊號，趨勢佔比濾網沒有可交易的 edge")
    L = ["# 數據獵手 輕量校準(對齊單一 SuperTrend 訊號)結果\n",
         f"- 宇宙：**{scope}**。訊號：ST翻多+RSI30~70+relVol≥VOL_MULT+排除漲跌停；"
         f"出場 CHAND×ATR22停損/+{scan.TP1_R}R停利/{TIME_STOP_BARS}根time-stop；成本 {COST_RT}% round-trip。",
         f"- 切分：代碼末位 偶=train({n_tr}檔有交易) / 奇=test({n_te}檔有交易)。",
         f"- 池濾網 = **趨勢佔比(近{scan.POOL_LOOKBACK}根 ADX≥{scan.ADX_TREND_THR} 的 trailing 滾動比例) "
         f"+ 流動性(近60根收盤×量滾動中位)**，在『進場那根 i』判定、只用過去資料 → "
         f"**可交易 OOS、非全期靜態選股(已修掉 look-ahead 樂觀偏誤)**。",
         f"- Fitness = 0.5×前瞻中位% + 3×命中停利比 + 0.4×中位R(樣本<{MIN_TRADES}淘汰)。\n",
         "## 最佳參數(train 網格)\n",
         "| 參數 | scan 現值 | 校準最佳 |", "|---|---|---|",
         f"| ST_MULT | {defaults['st_mult']} | **{best_params['st_mult']}** |",
         f"| VOL_MULT | {defaults['vol_mult']} | **{best_params['vol_mult']}** |",
         f"| CHAND_MULT | {defaults['chand_mult']} | **{best_params['chand_mult']}** |",
         f"| 池濾網 tf_min | {defaults['tf_min']} | **{best_params['tf_min']}** |\n",
         "## ★ 池濾網 前後對比(最佳參數，trailing 可交易)\n",
         "| 集合 | 全宇宙(不濾) | 濾後池(trailing) |", "|---|---|---|",
         f"| **train** | {_fmt_m(train_all)} | {_fmt_m(train_pool)} |",
         f"| **test(OOS)** | {_fmt_m(test_all)} | {_fmt_m(test_pool)} |",
         f"\n- train：有交易 {n_tr} 檔、其中池內 {np_tr} 檔；test：有交易 {n_te} 檔、池內 {np_te} 檔。",
         f"- 池濾網對 OOS 獲利檔比例：{test_all['prof_stock_ratio']*100:.0f}% → "
         f"**{test_pool['prof_stock_ratio']*100:.0f}%**({d_prof:+.0f}pp)；前瞻中位 "
         f"{test_all['fwd_med']:+.2f}% → **{test_pool['fwd_med']:+.2f}%**({d_fwd:+.2f}pp)。",
         f"- 判讀：{verdict}。\n",
         "## ⚠ 誠實註記\n",
         "- 池濾網的效益數字要看 **--full(全市場)** 才有鑑別度；**精選宇宙(125檔)幾乎不咬**——"
         "因為精選都是流動大型股、趨勢佔比/成交額本就在池內，濾不掉什麼。",
         "- 本版池特徵已從『全期靜態統計選股(含未來)』**修正為 trailing 滾動(只看過去、可交易 OOS)**；"
         "因此改善幅度通常**比含 look-ahead 的舊版縮水**，這裡的數字才是實際可落地的效益(不美化)。",
         "- 單一 SuperTrend 訊號『前瞻中位本就偏負』(與既有台股回測『無腦套非通用』結論一致)；"
         "池濾網+籌碼確認是把可用子集勝率往上拉的兩層，非萬靈丹。\n",
         "## 回寫決策\n", f"- {applied}\n",
         "## Grid 軌跡(train，前 12 高 fitness)\n",
         "| ST | VOL | CHAND | tf_min | n | fitness | 前瞻中位 | 命中停利 | 中位R | 獲利檔比 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in sorted(traj, key=lambda x: x["fitness"], reverse=True)[:12]:
        L.append(f"| {r['st_mult']} | {r['vol_mult']} | {r['chand_mult']} | {r['tf_min']} | "
                 f"{r['n']} | {r['fitness']:.3f} | {r['fwd_med']:+.2f}% | {r['tp_hit']*100:.0f}% | "
                 f"{r['med_r']:+.2f} | {r['prof_stock_ratio']*100:.0f}% |")
    L.append("")
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


def maybe_apply(best_params, defaults, test_all, test_pool, default_test_pool,
                allow: bool) -> str:
    """只在『明顯更好且 OOS 穩健』時回寫 scan.py。預設保守不寫。
    判準：最佳參數的 OOS 濾後池 fitness 顯著高於『scan 現值參數』的 OOS 濾後池(margin)。"""
    margin = test_pool["fitness"] - default_test_pool["fitness"]
    same = all(abs(best_params[k] - defaults[k]) < 1e-9
               for k in ("st_mult", "vol_mult", "chand_mult"))
    if same:
        return (f"最佳參數 = scan 現值(ST={defaults['st_mult']}/VOL={defaults['vol_mult']}/"
                f"CHAND={defaults['chand_mult']})，無需回寫(現值已最佳)。")
    if not allow:
        return (f"最佳參數與現值不同(OOS fitness margin={margin:+.3f})，但未帶 --apply，"
                f"僅建議不回寫。建議值：ST={best_params['st_mult']} VOL={best_params['vol_mult']} "
                f"CHAND={best_params['chand_mult']}。")
    if margin < 0.30:
        return (f"OOS 改善不足(margin={margin:+.3f} < 0.30)→ 不回寫(避免過度調參)。"
                f"CHAND_MULT=3.5 已被台股回測背書，維持現值。")
    # 明顯更好且 OOS 穩健 → 回寫
    _rewrite_scan_const("ST_MULT", best_params["st_mult"])
    _rewrite_scan_const("VOL_MULT", best_params["vol_mult"])
    _rewrite_scan_const("CHAND_MULT", best_params["chand_mult"])
    return (f"✓ 已回寫 scan.py：ST_MULT={best_params['st_mult']} VOL_MULT={best_params['vol_mult']} "
            f"CHAND_MULT={best_params['chand_mult']}(OOS fitness margin={margin:+.3f})。")


def _rewrite_scan_const(name: str, value) -> None:
    """安全地把 scan.py 內 `NAME = ...`(行內含註解保留)替換成新值。"""
    path = scan.HERE / "scan.py"
    txt = path.read_text(encoding="utf-8")
    if name == "ST_MULT":
        txt = re.sub(r"ST_PERIOD, ST_MULT = 10, [0-9.]+",
                     f"ST_PERIOD, ST_MULT = 10, {value}", txt)
    elif name == "VOL_MULT":
        txt = re.sub(r"^VOL_MULT = [0-9.]+", f"VOL_MULT = {value}", txt, flags=re.M)
    elif name == "CHAND_MULT":
        txt = re.sub(r"CHAND_LEN, CHAND_MULT = 22, [0-9.]+",
                     f"CHAND_LEN, CHAND_MULT = 22, {value}", txt)
    scan._atomic_write_text(path, txt)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全市場(慢)")
    ap.add_argument("--apply", action="store_true", help="允許回寫明顯更好的參數到 scan.py")
    args = ap.parse_args()

    rows = load_full_universe() if args.full else all_codes()
    print(f"[calibrate] 載入快取宇宙 {len(rows)} 檔…")
    stocks = load_stocks(rows)
    print(f"[calibrate] 有效(≥{MIN_BARS}根) {len(stocks)} 檔，預算指標序列…")
    pres = precompute(stocks)
    train, test = split(pres)
    print(f"[calibrate] train {len(train)} / test {len(test)} 檔，網格搜尋中…")

    best_params, best_m, traj = grid_search(train)
    print(f"[calibrate] train 最佳：{best_params} fitness={best_m['fitness']:.3f}")

    defaults = dict(st_mult=scan.ST_MULT, vol_mult=scan.VOL_MULT,
                    chand_mult=scan.CHAND_MULT, tf_min=scan.POOL_TREND_MIN)

    # 前後對比(train/test)用『最佳參數』
    train_all, train_pool, n_tr, np_tr = pool_compare(train, best_params)
    test_all, test_pool, n_te, np_te = pool_compare(test, best_params)

    # 現值參數在 test 濾後池的 fitness(回寫判準的對照基準)
    _, default_test_pool, _, _ = pool_compare(test, defaults)

    print("\n=== 池濾網前後對比 ===")
    print(f"train 全宇宙: {_fmt_m(train_all)}")
    print(f"train 濾後池: {_fmt_m(train_pool)}  ({np_tr}/{n_tr}檔)")
    print(f"test  全宇宙: {_fmt_m(test_all)}")
    print(f"test  濾後池: {_fmt_m(test_pool)}  ({np_te}/{n_te}檔)")

    applied = maybe_apply(best_params, defaults, test_all, test_pool,
                          default_test_pool, args.apply)
    print(f"\n[calibrate] 回寫決策：{applied}")

    scope = f"全市場 {len(stocks)} 檔" if args.full else f"精選宇宙 {len(stocks)} 檔"
    write_report(best_params, train_all, train_pool, test_all, test_pool,
                 n_tr, n_te, np_tr, np_te, traj, defaults, applied, scope)
    print(f"[calibrate] 報告 → {OUT_MD}")


if __name__ == "__main__":
    main()
