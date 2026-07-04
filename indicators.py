"""
indicators.py — 技術指標計算模組
輸入：pandas DataFrame（OHLCV，欄位：open, high, low, close, volume）
輸出：dict 含各指標數值
"""

import numpy as np
import pandas as pd
from typing import Optional


def _normalize_cols(df: pd.DataFrame) -> pd.DataFrame:
    """統一欄位名稱為小寫，相容 tw_stock_data（回傳大寫）和手動 DataFrame（小寫）"""
    return df.rename(columns={c: c.lower() for c in df.columns})


# ---------------------------------------------------------------------------
# 1. 移動平均線
# ---------------------------------------------------------------------------

def calc_ma(df: pd.DataFrame, periods: list[int] = [5, 10, 20, 60]) -> dict:
    """
    計算簡單移動平均線（SMA）。

    Args:
        df: OHLCV DataFrame，須含 'close' 欄位
        periods: 均線週期清單

    Returns:
        dict with keys:
            ma_{period}: 最新均線值
            price: 最新收盤價
            alignment: 'BULLISH' / 'BEARISH' / 'MIXED'
            above_ma: list of periods where price > MA
            below_ma: list of periods where price < MA
    """
    df = _normalize_cols(df)
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    result: dict = {"price": price, "ma": {}}

    above, below = [], []
    for p in periods:
        if len(close) < p:
            result["ma"][p] = None
            continue
        ma_val = float(close.rolling(window=p).mean().iloc[-1])
        result["ma"][p] = round(ma_val, 6)
        if price > ma_val:
            above.append(p)
        else:
            below.append(p)

    valid_periods = [p for p in periods if result["ma"].get(p) is not None]
    if not valid_periods:
        result["alignment"] = "INSUFFICIENT_DATA"
    elif len(above) == len(valid_periods):
        result["alignment"] = "BULLISH"   # 價格在所有均線之上
    elif len(below) == len(valid_periods):
        result["alignment"] = "BEARISH"   # 價格在所有均線之下
    else:
        result["alignment"] = "MIXED"

    result["above_ma"] = above
    result["below_ma"] = below
    return result


# ---------------------------------------------------------------------------
# 2. RSI
# ---------------------------------------------------------------------------

def calc_rsi(df: pd.DataFrame, period: int = 14) -> dict:
    """
    計算 RSI（Relative Strength Index）。

    Args:
        df: OHLCV DataFrame，須含 'close' 欄位
        period: 計算週期，預設 14

    Returns:
        dict with keys:
            rsi: RSI 數值（0-100）
            signal: 'OVERBOUGHT' / 'OVERSOLD' / 'NEUTRAL'
            overbought_threshold: 70
            oversold_threshold: 30
    """
    df = _normalize_cols(df)
    close = df["close"].astype(float)
    if len(close) < period + 1:
        return {"rsi": None, "signal": "INSUFFICIENT_DATA",
                "overbought_threshold": 70, "oversold_threshold": 30}

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing（等同 EMA with alpha=1/period）
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_series = 100 - (100 / (1 + rs))
    rsi_val = float(rsi_series.iloc[-1])

    if np.isnan(rsi_val):
        signal = "INSUFFICIENT_DATA"
    elif rsi_val >= 70:
        signal = "OVERBOUGHT"
    elif rsi_val <= 30:
        signal = "OVERSOLD"
    else:
        signal = "NEUTRAL"

    return {
        "rsi": round(rsi_val, 2),
        "signal": signal,
        "overbought_threshold": 70,
        "oversold_threshold": 30,
    }


# ---------------------------------------------------------------------------
# 3. MACD
# ---------------------------------------------------------------------------

def calc_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> dict:
    """
    計算 MACD（Moving Average Convergence Divergence）。

    Args:
        df: OHLCV DataFrame，須含 'close' 欄位
        fast: 快線 EMA 週期，預設 12
        slow: 慢線 EMA 週期，預設 26
        signal: 訊號線 EMA 週期，預設 9

    Returns:
        dict with keys:
            macd: MACD 線值
            signal_line: 訊號線值
            histogram: 柱狀圖值（macd - signal_line）
            crossover: 'BULLISH_CROSS' / 'BEARISH_CROSS' / 'NONE'
            trend: 'BULLISH' / 'BEARISH'
    """
    df = _normalize_cols(df)
    close = df["close"].astype(float)
    min_len = slow + signal
    if len(close) < min_len:
        return {
            "macd": None,
            "signal_line": None,
            "histogram": None,
            "crossover": "INSUFFICIENT_DATA",
            "trend": "INSUFFICIENT_DATA",
        }

    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line

    macd_val = float(macd_line.iloc[-1])
    sig_val = float(signal_line.iloc[-1])
    hist_val = float(histogram.iloc[-1])

    # 黃金交叉 / 死亡交叉（前一根 vs 這根）
    prev_hist = float(histogram.iloc[-2]) if len(histogram) >= 2 else 0.0
    if prev_hist < 0 and hist_val >= 0:
        crossover = "BULLISH_CROSS"
    elif prev_hist > 0 and hist_val <= 0:
        crossover = "BEARISH_CROSS"
    else:
        crossover = "NONE"

    trend = "BULLISH" if macd_val > sig_val else "BEARISH"

    return {
        "macd": round(macd_val, 6),
        "signal_line": round(sig_val, 6),
        "histogram": round(hist_val, 6),
        "crossover": crossover,
        "trend": trend,
    }


# ---------------------------------------------------------------------------
# 4. SuperTrend
# ---------------------------------------------------------------------------

def calc_supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> dict:
    """
    計算 SuperTrend 指標（ATR 基礎）。

    Args:
        df: OHLCV DataFrame，須含 'high', 'low', 'close' 欄位
        period: ATR 週期，預設 10
        multiplier: ATR 倍數，預設 3.0

    Returns:
        dict with keys:
            supertrend: SuperTrend 線值
            direction: 'UP' / 'DOWN'
            atr: ATR 值
    """
    df = _normalize_cols(df)
    if len(df) < period + 1:
        return {"supertrend": None, "direction": None, "atr": None}

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    # ATR（Wilder 平滑）
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    hl2 = (high + low) / 2
    upper_band = hl2 + multiplier * atr
    lower_band = hl2 - multiplier * atr

    # 逐步計算 SuperTrend
    st = pd.Series(index=df.index, dtype=float)
    direction = pd.Series(index=df.index, dtype=int)  # 1=UP, -1=DOWN

    for i in range(period, len(df)):
        idx = df.index[i]
        prev_idx = df.index[i - 1]

        # 調整帶值（避免帶值朝不利方向移動）
        if i > period:
            prev_lower = st.iloc[i - 1] if direction.iloc[i - 1] == 1 else lower_band.iloc[i]
            prev_upper = upper_band.iloc[i] if direction.iloc[i - 1] == 1 else st.iloc[i - 1]
            lower_band.iloc[i] = max(lower_band.iloc[i], prev_lower) if direction.iloc[i - 1] == 1 else lower_band.iloc[i]
            upper_band.iloc[i] = min(upper_band.iloc[i], prev_upper) if direction.iloc[i - 1] == -1 else upper_band.iloc[i]

        prev_dir = direction.iloc[i - 1] if i > period else 1
        prev_st = st.iloc[i - 1] if i > period else lower_band.iloc[i]

        if prev_dir == 1:  # 前一根向上
            if close.iloc[i] < lower_band.iloc[i]:
                direction.iloc[i] = -1
                st.iloc[i] = upper_band.iloc[i]
            else:
                direction.iloc[i] = 1
                st.iloc[i] = lower_band.iloc[i]
        else:  # 前一根向下
            if close.iloc[i] > upper_band.iloc[i]:
                direction.iloc[i] = 1
                st.iloc[i] = lower_band.iloc[i]
            else:
                direction.iloc[i] = -1
                st.iloc[i] = upper_band.iloc[i]

    last_valid = st.dropna()
    if last_valid.empty:
        return {"supertrend": None, "direction": None, "atr": None}

    last_dir = int(direction.dropna().iloc[-1])
    return {
        "supertrend": round(float(last_valid.iloc[-1]), 6),
        "direction": "UP" if last_dir == 1 else "DOWN",
        "atr": round(float(atr.iloc[-1]), 6),
    }


# ---------------------------------------------------------------------------
# 5. Bollinger Bands
# ---------------------------------------------------------------------------

def calc_bollinger(
    df: pd.DataFrame,
    period: int = 20,
    std: float = 2.0,
) -> dict:
    """
    計算布林通道（Bollinger Bands）。

    Args:
        df: OHLCV DataFrame，須含 'close' 欄位
        period: 移動平均週期，預設 20
        std: 標準差倍數，預設 2.0

    Returns:
        dict with keys:
            upper: 上軌
            middle: 中軌（SMA）
            lower: 下軌
            percent_b: %B 值（0=下軌, 0.5=中軌, 1=上軌）
            bandwidth: 帶寬（(upper-lower)/middle * 100）
            position: 'ABOVE_UPPER' / 'NEAR_UPPER' / 'MIDDLE' / 'NEAR_LOWER' / 'BELOW_LOWER'
    """
    df = _normalize_cols(df)
    close = df["close"].astype(float)
    if len(close) < period:
        return {
            "upper": None, "middle": None, "lower": None,
            "percent_b": None, "bandwidth": None, "position": "INSUFFICIENT_DATA",
        }

    rolling = close.rolling(window=period)
    middle = rolling.mean()
    sigma = rolling.std(ddof=1)

    upper = middle + std * sigma
    lower = middle - std * sigma

    price = float(close.iloc[-1])
    mid_val = float(middle.iloc[-1])
    up_val = float(upper.iloc[-1])
    lo_val = float(lower.iloc[-1])
    band_width = up_val - lo_val

    percent_b = (price - lo_val) / band_width if band_width != 0 else 0.5
    bandwidth = (band_width / mid_val) * 100 if mid_val != 0 else 0.0

    if price > up_val:
        position = "ABOVE_UPPER"
    elif price >= up_val * 0.995:
        position = "NEAR_UPPER"
    elif price <= lo_val:
        position = "BELOW_LOWER"
    elif price <= lo_val * 1.005:
        position = "NEAR_LOWER"
    else:
        position = "MIDDLE"

    return {
        "upper": round(up_val, 6),
        "middle": round(mid_val, 6),
        "lower": round(lo_val, 6),
        "percent_b": round(percent_b, 4),
        "bandwidth": round(bandwidth, 4),
        "position": position,
    }


# ---------------------------------------------------------------------------
# 5b. 擴充指標（OBV / DMI / 威廉%R / CCI / 寶塔線）— 對齊三竹 24 指標
#     皆為 additive 純函式，回傳最新值 + 訊號標籤；不動既有指標契約。
# ---------------------------------------------------------------------------

def calc_obv(df: pd.DataFrame) -> dict:
    """能量潮 OBV：量能累積。趨勢＝OBV 相對其 20 期 EMA（量價同步/背離參考）。"""
    df = _normalize_cols(df)
    close = df["close"].astype(float)
    vol = df["volume"].astype(float)
    direction = np.sign(close.diff().fillna(0.0))
    obv = (direction * vol).cumsum()
    if len(obv) < 2:
        return {"obv": None, "trend": None}
    obv_ema = obv.ewm(span=20, adjust=False).mean()
    return {"obv": float(obv.iloc[-1]),
            "obv_ema": float(obv_ema.iloc[-1]),
            "trend": "up" if obv.iloc[-1] >= obv_ema.iloc[-1] else "down"}


def calc_dmi(df: pd.DataFrame, period: int = 14) -> dict:
    """動向指標 DMI（Wilder）：+DI / -DI / ADX。+DI>-DI 為多方動能。"""
    df = _normalize_cols(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    if len(close) < period + 1:
        return {"plus_di": None, "minus_di": None, "adx": None, "signal": None}
    up = high.diff()
    dn = -low.diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = pd.concat([(high - low),
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    # Wilder 平滑（用 EMA alpha=1/period 近似）
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    plus_di = 100 * pd.Series(plus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100 * pd.Series(minus_dm, index=high.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    p, m = float(plus_di.iloc[-1]), float(minus_di.iloc[-1])
    return {"plus_di": round(p, 1), "minus_di": round(m, 1),
            "adx": round(float(adx.iloc[-1]), 1) if not np.isnan(adx.iloc[-1]) else None,
            "signal": "多" if p >= m else "空"}


def calc_williams_r(df: pd.DataFrame, period: int = 14) -> dict:
    """威廉指標 %R：-100~0。>-20 超買、<-80 超賣。"""
    df = _normalize_cols(df)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    if len(close) < period:
        return {"williams_r": None, "zone": None}
    hh = high.rolling(period).max().iloc[-1]
    ll = low.rolling(period).min().iloc[-1]
    if hh == ll:
        wr = -50.0
    else:
        wr = -100.0 * (hh - close.iloc[-1]) / (hh - ll)
    zone = "超買" if wr > -20 else ("超賣" if wr < -80 else "中性")
    return {"williams_r": round(float(wr), 1), "zone": zone}


def calc_cci(df: pd.DataFrame, period: int = 20) -> dict:
    """順勢指標 CCI：>+100 偏強、<-100 偏弱。"""
    df = _normalize_cols(df)
    tp = (df["high"].astype(float) + df["low"].astype(float) + df["close"].astype(float)) / 3
    if len(tp) < period:
        return {"cci": None, "zone": None}
    sma = tp.rolling(period).mean()
    md = tp.rolling(period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    denom = 0.015 * md.iloc[-1]
    if not denom:
        return {"cci": None, "zone": None}
    cci = (tp.iloc[-1] - sma.iloc[-1]) / denom
    zone = "超買" if cci > 100 else ("超賣" if cci < -100 else "中性")
    return {"cci": round(float(cci), 1), "zone": zone}


def calc_tower(df: pd.DataFrame, lookback: int = 3) -> dict:
    """寶塔線（三線反轉近似）：翻紅＝收盤突破前 N 根最高收、翻黑＝跌破前 N 根最低收。
    回最新顏色 + 是否剛翻轉（反轉訊號）。"""
    df = _normalize_cols(df)
    close = df["close"].astype(float).reset_index(drop=True)
    if len(close) < lookback + 2:
        return {"tower": None, "flip": None}
    color = "紅"
    prev_color = "紅"
    flip = False
    for i in range(lookback, len(close)):
        prev_hi = close.iloc[i - lookback:i].max()
        prev_lo = close.iloc[i - lookback:i].min()
        prev_color = color
        if color == "黑" and close.iloc[i] > prev_hi:
            color = "紅"
        elif color == "紅" and close.iloc[i] < prev_lo:
            color = "黑"
    flip = (color != prev_color)
    return {"tower": color, "flip": bool(flip),
            "signal": "翻紅(轉多)" if (flip and color == "紅") else
                      ("翻黑(轉空)" if (flip and color == "黑") else
                       ("紅(多)" if color == "紅" else "黑(空)"))}


def resample_ohlc(df: pd.DataFrame, rule: str, bars: int = 40) -> list:
    """把日線 resample 成週(rule='W')/月(rule='M')K，回最近 bars 根 [o,h,l,c] 陣列（給前端畫 K）。"""
    d = _normalize_cols(df).copy()
    if not isinstance(d.index, pd.DatetimeIndex):
        return []
    agg = d.resample(rule).agg({"open": "first", "high": "max", "low": "min", "close": "last"})
    agg = agg.dropna()
    out = []
    for _, r in agg.tail(bars).iterrows():
        out.append([round(float(r["open"]), 2), round(float(r["high"]), 2),
                    round(float(r["low"]), 2), round(float(r["close"]), 2)])
    return out


# ---------------------------------------------------------------------------
# 6. 完整分析（Markdown 摘要）
# ---------------------------------------------------------------------------

def full_analysis(df: pd.DataFrame, ticker: str) -> str:
    """
    呼叫所有指標，回傳 Markdown 格式技術分析摘要。

    Args:
        df: OHLCV DataFrame
        ticker: 交易對/股票代碼（純顯示用）

    Returns:
        Markdown 字串
    """
    df = _normalize_cols(df)
    close = df["close"].astype(float)
    price = float(close.iloc[-1])
    prev_price = float(close.iloc[-2]) if len(close) >= 2 else price
    change = price - prev_price
    change_pct = (change / prev_price) * 100 if prev_price != 0 else 0.0
    arrow = "▲" if change >= 0 else "▼"
    sign = "+" if change >= 0 else ""

    ma = calc_ma(df)
    rsi = calc_rsi(df)
    macd = calc_macd(df)
    st = calc_supertrend(df)
    bb = calc_bollinger(df)

    # ---------- 均線區塊 ----------
    ma_lines = []
    for p, v in sorted(ma["ma"].items()):
        if v is not None:
            rel = "上方" if price > v else "下方"
            ma_lines.append(f"  - MA{p}: `{v:.4f}` （價格在{rel}）")
    ma_block = "\n".join(ma_lines) if ma_lines else "  - 資料不足"

    alignment_map = {
        "BULLISH": "多頭排列（價格在所有均線之上）",
        "BEARISH": "空頭排列（價格在所有均線之下）",
        "MIXED": "混合排列",
        "INSUFFICIENT_DATA": "資料不足",
    }
    alignment_str = alignment_map.get(ma["alignment"], ma["alignment"])

    # ---------- RSI 區塊 ----------
    rsi_val = rsi["rsi"]
    rsi_signal_map = {
        "OVERBOUGHT": "超買（>70），注意回檔風險",
        "OVERSOLD": "超賣（<30），可能反彈",
        "NEUTRAL": "中性區間（30-70）",
        "INSUFFICIENT_DATA": "資料不足",
    }
    rsi_str = f"`{rsi_val}`  →  {rsi_signal_map.get(rsi['signal'], rsi['signal'])}" if rsi_val is not None else "資料不足"

    # ---------- MACD 區塊 ----------
    if macd["macd"] is not None:
        cross_map = {
            "BULLISH_CROSS": "本根出現黃金交叉 ★",
            "BEARISH_CROSS": "本根出現死亡交叉 ★",
            "NONE": "無交叉",
            "INSUFFICIENT_DATA": "資料不足",
        }
        macd_trend_map = {"BULLISH": "多頭（MACD > Signal）", "BEARISH": "空頭（MACD < Signal）"}
        macd_str = (
            f"MACD: `{macd['macd']:.6f}`  |  Signal: `{macd['signal_line']:.6f}`  |  Hist: `{macd['histogram']:.6f}`\n"
            f"  - 趨勢：{macd_trend_map.get(macd['trend'], macd['trend'])}\n"
            f"  - 交叉：{cross_map.get(macd['crossover'], macd['crossover'])}"
        )
    else:
        macd_str = "資料不足"

    # ---------- SuperTrend 區塊 ----------
    if st["supertrend"] is not None:
        st_dir_map = {"UP": "UP（多頭）", "DOWN": "DOWN（空頭）"}
        st_str = (
            f"SuperTrend: `{st['supertrend']:.4f}`  |  方向: **{st_dir_map.get(st['direction'], st['direction'])}**  |  ATR: `{st['atr']:.4f}`"
        )
    else:
        st_str = "資料不足"

    # ---------- 布林通道區塊 ----------
    if bb["upper"] is not None:
        pos_map = {
            "ABOVE_UPPER": "突破上軌（強勢，但可能過熱）",
            "NEAR_UPPER": "接近上軌（偏強）",
            "MIDDLE": "通道中段",
            "NEAR_LOWER": "接近下軌（偏弱）",
            "BELOW_LOWER": "跌破下軌（弱勢，可能超跌）",
            "INSUFFICIENT_DATA": "資料不足",
        }
        bb_str = (
            f"上軌: `{bb['upper']:.4f}`  |  中軌: `{bb['middle']:.4f}`  |  下軌: `{bb['lower']:.4f}`\n"
            f"  - %B: `{bb['percent_b']:.4f}`  |  帶寬: `{bb['bandwidth']:.2f}%`\n"
            f"  - 位置: {pos_map.get(bb['position'], bb['position'])}"
        )
    else:
        bb_str = "資料不足"

    # ---------- 綜合結論 ----------
    bullish_signals, bearish_signals = 0, 0

    if ma["alignment"] == "BULLISH":
        bullish_signals += 1
    elif ma["alignment"] == "BEARISH":
        bearish_signals += 1

    if rsi.get("signal") == "OVERSOLD":
        bullish_signals += 1
    elif rsi.get("signal") == "OVERBOUGHT":
        bearish_signals += 1

    if macd.get("trend") == "BULLISH":
        bullish_signals += 1
    elif macd.get("trend") == "BEARISH":
        bearish_signals += 1

    if st.get("direction") == "UP":
        bullish_signals += 1
    elif st.get("direction") == "DOWN":
        bearish_signals += 1

    if bb.get("position") in ("ABOVE_UPPER", "NEAR_UPPER"):
        bullish_signals += 1
    elif bb.get("position") in ("BELOW_LOWER", "NEAR_LOWER"):
        bearish_signals += 1

    total = bullish_signals + bearish_signals
    if total == 0:
        conclusion = "資料不足，無法給出結論。"
    elif bullish_signals > bearish_signals * 2:
        conclusion = f"多數指標偏多（{bullish_signals}/{total}），短線傾向偏多，但需留意量能確認。"
    elif bearish_signals > bullish_signals * 2:
        conclusion = f"多數指標偏空（{bearish_signals}/{total}），短線傾向偏空，建議觀望或設好停損。"
    else:
        conclusion = f"多空訊號分歧（多:{bullish_signals} 空:{bearish_signals}），建議等待更明確趨勢確認後再入場。"

    # ---------- 組合 Markdown ----------
    report = f"""# {ticker} 技術分析報告

## 最新價格
| 指標 | 數值 |
|------|------|
| 收盤價 | `{price:.4f}` |
| 漲跌 | {arrow} `{sign}{change:.4f}` ({sign}{change_pct:.2f}%) |

---

## 均線（MA）
{ma_block}

**排列狀態**：{alignment_str}

---

## RSI（{14}）
- 數值：{rsi_str}

---

## MACD（12, 26, 9）
- {macd_str}

---

## SuperTrend（10, 3.0）
- {st_str}

---

## 布林通道（20, 2σ）
- {bb_str}

---

## 綜合結論
> {conclusion}

*本報告由純 pandas/numpy 計算，基於真實 K 線數據，僅供參考，不構成投資建議。*
"""
    return report.strip()


# ---------------------------------------------------------------------------
# 快速自測（直接執行此檔時）
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 產生 200 根假 K 線（隨機漫步）以驗證所有函式
    np.random.seed(42)
    n = 200
    close_prices = 100 * np.cumprod(1 + np.random.normal(0.001, 0.02, n))
    high_prices = close_prices * (1 + np.abs(np.random.normal(0, 0.01, n)))
    low_prices = close_prices * (1 - np.abs(np.random.normal(0, 0.01, n)))
    open_prices = close_prices * (1 + np.random.normal(0, 0.005, n))
    volume = np.random.randint(1000, 50000, n)

    test_df = pd.DataFrame({
        "open": open_prices,
        "high": high_prices,
        "low": low_prices,
        "close": close_prices,
        "volume": volume,
    })

    print("=== calc_ma ===")
    print(calc_ma(test_df))

    print("\n=== calc_rsi ===")
    print(calc_rsi(test_df))

    print("\n=== calc_macd ===")
    print(calc_macd(test_df))

    print("\n=== calc_supertrend ===")
    print(calc_supertrend(test_df))

    print("\n=== calc_bollinger ===")
    print(calc_bollinger(test_df))

    print("\n=== full_analysis ===")
    print(full_analysis(test_df, "BTC/USDT"))
