# -*- coding: utf-8 -*-
"""
validate.py — 數據獵手強弱分/市場溫度的「預測力」抽樣回測(best-effort)

用 twdata/cache 既有日線，對兩件事做單調性檢定，確認權重不是亂湊：
  檢定A：個股『強弱分』分位 vs 未來 5 日報酬 → 分數越高，前瞻報酬應越高(單調遞增)
  檢定B：『市場溫度』分桶 vs 大盤(0050)次日報酬 → 溫度越高，次日大盤報酬應越高

歷史指標一律用『截至 t、不看未來』的滾動序列(與 scan.py 同公式向量化)，fwd 報酬才不偷看。
這是「當下快照」式的統計傾向，非保證；做不出來就交腳本+初步結果，不 block 主線。

用法：
  python validate.py                 # 精選宇宙
  python validate.py --full          # 全市場(慢)
  python validate.py --bins 5
"""
from __future__ import annotations

import argparse
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

import scan                                    # 重用 _read_cache / 宇宙 / 參數
from universe import all_codes, load_full_universe


# ── 向量化歷史指標(與 scan.py 同義，但回整條序列；皆不看未來) ──────────────
def _rsi_series(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    ag = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    al = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = ag / al.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _pctb_series(close: pd.Series, period: int = 20, std: float = 2.0) -> pd.Series:
    mid = close.rolling(period).mean()
    sig = close.rolling(period).std(ddof=1)
    upper, lower = mid + std * sig, mid - std * sig
    width = (upper - lower).replace(0, np.nan)
    return (close - lower) / width


def _adx_series(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    up, dn = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=1 / period, min_periods=period, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _st_dir_series(df: pd.DataFrame, period: int = 10, mult: float = 3.0) -> np.ndarray:
    """完整 SuperTrend 方向序列(+1/-1)，同 scan._st_dirs 邏輯。"""
    n = len(df)
    d = np.zeros(n, dtype=int)
    if n < period + 2:
        return d
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    hl2 = (high + low) / 2
    ub = (hl2 + mult * atr).to_numpy()
    lb = (hl2 - mult * atr).to_numpy()
    c = close.to_numpy()
    st = np.full(n, np.nan)
    for i in range(period, n):
        if i > period:
            if d[i - 1] == 1:
                lb[i] = max(lb[i], st[i - 1])
            else:
                ub[i] = min(ub[i], st[i - 1])
        prev_dir = d[i - 1] if i > period else 1
        if prev_dir == 1:
            if c[i] < lb[i]:
                d[i] = -1; st[i] = ub[i]
            else:
                d[i] = 1; st[i] = lb[i]
        else:
            if c[i] > ub[i]:
                d[i] = 1; st[i] = lb[i]
            else:
                d[i] = -1; st[i] = ub[i]
    return d


def _score_series(df_low: pd.DataFrame) -> pd.Series:
    """與 scan._analyse_core 同公式的『強弱分』整條序列(0-100，正交四維)。"""
    close = df_low["close"].astype(float)
    vol = df_low["volume"].astype(float)
    rsi = _rsi_series(close, scan.RSI_PERIOD)
    pctb = _pctb_series(close, 20, 2.0)
    adx = _adx_series(df_low, scan.ADX_PERIOD)
    stdir = pd.Series(_st_dir_series(df_low, scan.ST_PERIOD, scan.ST_MULT), index=df_low.index)
    relvol = vol / vol.rolling(20).mean().shift(1)

    trend_s = 12.5 * (stdir > 0).astype(float) + 12.5 * (adx / 40.0).clip(0, 1)
    pos_s = 25.0 * pctb.clip(0, 1)
    mom_s = 25.0 * (rsi / 100.0)
    vol_s = 25.0 * ((relvol - 0.5) / 1.5).clip(0, 1)
    return (trend_s + pos_s + mom_s + vol_s).clip(0, 100)


# ── 檢定A：強弱分分位 vs 未來5日報酬 ────────────────────────────────────────
def check_score_vs_fwd(rows, bins: int = 5, fwd: int = 5) -> pd.DataFrame | None:
    pairs_s, pairs_r = [], []
    used = 0
    for code, _, _ in rows:
        df = scan._read_cache(code)
        if df is None or len(df) < 120:
            continue
        d = df.rename(columns={x: x.lower() for x in df.columns})
        close = d["close"].astype(float)
        score = _score_series(d)
        fwd_ret = close.shift(-fwd) / close - 1.0
        m = score.notna() & fwd_ret.notna()
        if m.sum() < 30:
            continue
        pairs_s.append(score[m].to_numpy())
        pairs_r.append(fwd_ret[m].to_numpy())
        used += 1
    if not pairs_s:
        print("[validate] A：可用樣本不足")
        return None
    s = np.concatenate(pairs_s)
    r = np.concatenate(pairs_r) * 100.0          # %
    # 依分數分位切桶
    qs = np.quantile(s, np.linspace(0, 1, bins + 1))
    qs[-1] += 1e-9
    lab = np.clip(np.digitize(s, qs[1:-1]), 0, bins - 1)
    out = []
    for b in range(bins):
        mask = lab == b
        if mask.sum() == 0:
            continue
        out.append(dict(bucket=f"Q{b+1}", n=int(mask.sum()),
                        score_lo=round(float(s[mask].min()), 1),
                        score_hi=round(float(s[mask].max()), 1),
                        fwd5_mean_pct=round(float(r[mask].mean()), 3),
                        fwd5_median_pct=round(float(np.median(r[mask])), 3),
                        win_pct=round(float((r[mask] > 0).mean() * 100), 1)))
    tbl = pd.DataFrame(out)
    means = tbl["fwd5_mean_pct"].to_numpy()
    mono = bool(np.all(np.diff(means) >= -0.05))   # 容忍微幅雜訊的單調遞增
    spread = round(float(means[-1] - means[0]), 3)
    print(f"\n=== 檢定A｜強弱分分位 vs 未來{fwd}日報酬（{used} 檔，{len(s):,} 樣本）===")
    print(tbl.to_string(index=False))
    print(f"單調遞增: {'✓' if mono else '✗'}　最高分位−最低分位 fwd{fwd} 報酬差: {spread:+.3f}%"
          f"（>0 代表分數有方向性預測力）")
    return tbl


# ── 檢定B：市場溫度分桶 vs 大盤(0050)次日報酬 ──────────────────────────────
def check_temp_vs_index(rows, bins: int = 3) -> pd.DataFrame | None:
    # 對齊精選宇宙日收盤，逐日算 breadth(站上20MA比例)+avgRSI → 溫度代理
    closes = {}
    for code, _, _ in rows:
        df = scan._read_cache(code)
        if df is None or len(df) < 80:
            continue
        d = df.rename(columns={x: x.lower() for x in df.columns})
        closes[code] = d["close"].astype(float)
    if "0050" not in closes or len(closes) < 20:
        print("[validate] B：缺 0050 或樣本不足，略過")
        return None
    px = pd.DataFrame(closes).sort_index()
    px = px[px.index >= px.index[-1] - pd.Timedelta(days=900)]   # 近~3年
    ma20 = px.rolling(20).mean()
    above = (px > ma20)
    breadth = above.mean(axis=1) * 100.0
    # 各股 RSI 再取截面均值
    rsi_df = pd.DataFrame({c: _rsi_series(px[c], scan.RSI_PERIOD) for c in px.columns})
    avg_rsi = rsi_df.mean(axis=1)
    temp = (0.5 * avg_rsi + 0.5 * breadth).dropna()

    idx = closes["0050"].reindex(temp.index)
    nxt_ret = (idx.shift(-1) / idx - 1.0) * 100.0
    df2 = pd.DataFrame({"temp": temp, "nxt": nxt_ret}).dropna()
    if len(df2) < 60:
        print("[validate] B：對齊後樣本不足，略過")
        return None
    qs = np.quantile(df2["temp"], np.linspace(0, 1, bins + 1))
    qs[-1] += 1e-9
    lab = np.clip(np.digitize(df2["temp"], qs[1:-1]), 0, bins - 1)
    names = {0: "低溫", 1: "中溫", 2: "高溫"} if bins == 3 else {i: f"B{i+1}" for i in range(bins)}
    out = []
    for b in range(bins):
        mask = lab == b
        if mask.sum() == 0:
            continue
        sub = df2[mask]
        out.append(dict(bucket=names.get(b, f"B{b+1}"), n=int(mask.sum()),
                        temp_lo=round(float(sub["temp"].min()), 1),
                        temp_hi=round(float(sub["temp"].max()), 1),
                        idx_next_mean_pct=round(float(sub["nxt"].mean()), 4),
                        up_pct=round(float((sub["nxt"] > 0).mean() * 100), 1)))
    tbl = pd.DataFrame(out)
    means = tbl["idx_next_mean_pct"].to_numpy()
    mono = bool(np.all(np.diff(means) >= -0.02))
    print(f"\n=== 檢定B｜市場溫度分桶 vs 0050 次日報酬（{len(df2):,} 交易日）===")
    print(tbl.to_string(index=False))
    print(f"單調遞增: {'✓' if mono else '✗'}　高溫−低溫 次日報酬差: "
          f"{means[-1]-means[0]:+.4f}%（>0 代表溫度有擇時傾向）")
    return tbl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="全市場(慢)")
    ap.add_argument("--bins", type=int, default=5, help="檢定A 分位數")
    args = ap.parse_args()
    rows = load_full_universe() if args.full else all_codes()
    print(f"[validate] 宇宙 {len(rows)} 檔，讀快取做抽樣回測…")
    check_score_vs_fwd(rows, bins=args.bins, fwd=5)
    check_temp_vs_index(all_codes(), bins=3)   # 溫度檢定用精選(對齊乾淨)
    print("\n[validate] 完成。⚠ 這是統計傾向非保證；建議定期重跑。")


if __name__ == "__main__":
    main()
