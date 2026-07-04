# -*- coding: utf-8 -*-
"""
query.py — 數據獵手「個股查詢」

打代號(2330)或名稱(台積電)查任意上市櫃個股的完整分析，欄位對齊掃描卡片 schema，
讓前端 popover / 詳情視窗直接重用。指標與訊號一律重用 scan 的既有邏輯(不重寫)，
籌碼面重用 chips/margin/tdcc 的 load(缺資料優雅降級為 None)。

對外 API：
  analyze_stock(code, live=False) -> dict   單一個股完整分析(含指標/籌碼/OHLC)
  search_stocks(q, limit=10)      -> list   代號/名稱模糊比對(給自動完成、名稱轉代號)

CLI 自測：
  python query.py 2330       # 用代號查
  python query.py 台積電      # 用名稱查
  python query.py 2330 --live # 盤中用證交所即時價覆蓋最後一根
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))       # indicators / notify
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import scan                                 # 重用資料載入 / 指標 / 訊號 / 籌碼 merge  # noqa: E402
import universe                             # 名稱↔代號、產業別  # noqa: E402


# ── 代號 / 名稱解析 ──────────────────────────────────────────────────────────
def _twstock_codes():
    """回傳 twstock.codes(dict)；未安裝則 None。"""
    try:
        import twstock
        return twstock.codes
    except Exception:
        return None


def _is_code(q: str) -> bool:
    """純數字(含 0050 / 00878 這類 ETF 代號)視為代號，其餘(中文名)走名稱解析。"""
    return q.isdigit()


def _yf_name(code: str) -> str | None:
    """twstock 未收錄時，best-effort 用 yfinance shortName 取名(硬性 timeout 防卡)。"""
    try:
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor
    except Exception:
        return None

    def _grab(sym: str):
        info = getattr(yf.Ticker(sym), "info", {}) or {}
        return info.get("shortName") or info.get("longName")

    with ThreadPoolExecutor(max_workers=1) as ex:
        for suf in (".TW", ".TWO"):
            try:
                n = ex.submit(_grab, code + suf).result(timeout=4)
                if n:
                    return str(n)
            except Exception:
                continue
    return None


def _meta(code: str) -> tuple[str, str]:
    """(name, industry)：優先 twstock(含 ETF)，退精選宇宙，再退 yfinance shortName，最後退代號本身。"""
    codes = _twstock_codes()
    if codes is not None:
        info = codes.get(code)
        if info is not None:
            return info.name, (info.group or "").strip() or "其他"
    name = universe.code_to_name().get(code)
    ind = universe.code_to_industry().get(code)
    if name:
        return name, ind or "其他"
    # twstock 代碼表可能過時(未收錄較新 ETF) → 直接用 yfinance 取名，抓不到就顯示代號本身
    return (_yf_name(code) or code), "其他"


def search_stocks(q: str, limit: int = 10) -> list[dict]:
    """代號/名稱模糊比對，回傳 [{code, name, industry}, ...] 前 limit 筆。
    排序：完全等於代號 > 代號開頭 > 名稱完全相符 > 名稱包含 > 其他包含。"""
    q = (q or "").strip()
    if not q:
        return []

    def _rank(code: str, name: str) -> int:
        if q == code:
            return 0
        if code.startswith(q):
            return 1
        if q == name:
            return 2
        if q in name:
            return 3
        return 4

    # 完全不做 type 過濾：股票/ETF/受益證券/TDR/特別股/存託憑證/權證…全收(上市+上櫃)。
    # 權證有數萬檔，不『排除』而是排在最後(warrant_flag=1)，讓真標的照樣排前面、搜尋不被洗版。
    hits: list[tuple[int, int, str, dict]] = []
    codes = _twstock_codes()
    if codes is not None:
        for code, info in codes.items():
            if info.market not in ("上市", "上櫃"):
                continue
            name = info.name
            if q not in code and q not in name:
                continue
            ind = (info.group or "").strip() or "其他"
            warrant = 1 if "權證" in (info.type or "") else 0
            hits.append((_rank(code, name), warrant, code,
                         {"code": code, "name": name, "industry": ind}))
    else:
        # twstock 不可用 → 退回全市場宇宙(仍缺才退精選)；此路徑無權證，warrant 恆 0
        pool = universe.load_full_universe() or universe.all_codes()
        for code, name, ind in pool:
            if q not in code and q not in name:
                continue
            hits.append((_rank(code, name), 0, code,
                         {"code": code, "name": name, "industry": ind}))

    hits.sort(key=lambda h: (h[0], h[1], h[2]))
    return [h[3] for h in hits[:limit]]


def _resolve_code(q: str) -> str | None:
    """把使用者輸入轉成代號：純數字直接視為代號；中文名則模糊比對取最佳。"""
    q = (q or "").strip()
    if not q:
        return None
    if _is_code(q):
        return q
    hits = search_stocks(q, limit=1)
    return hits[0]["code"] if hits else None


# ── 資料載入(單檔) ───────────────────────────────────────────────────────────
def _run_bounded(fn, timeout: float, default=None):
    """在硬性 timeout 內跑 fn；逾時/例外都回 default(逾時的背景執行緒任其自然結束，主線程不等)。
    互動查詢絕不可被單一慢速網路呼叫無限拖住。"""
    from concurrent.futures import ThreadPoolExecutor
    try:
        with ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(fn).result(timeout=timeout)
    except Exception:
        return default


# 短期 df 記憶化：開一檔詳情前端會同時打 /api/stock + /api/analyst，兩者都 _load_df 同一檔
# → 快取讀取+官方回補+即時覆蓋做兩次。用 (code,live)→(ts,df) 短 TTL memo 讓第二次秒回。
_DF_MEMO: dict = {}
_DF_TTL = 20.0   # 秒；夠涵蓋同一次開窗的並發呼叫，又不至於拿到過時即時價


def _load_df(code: str, live: bool, yf_timeout: float = 10.0):
    """單檔 OHLCV：快取優先(本地、秒回)，缺則 yfinance(.TW→.TWO)但**硬性 ≤yf_timeout 秒**，
    逾時就當抓不到回 None(上層轉 {ok:false,error})，絕不無限等。live=True 再用即時價覆蓋(另有 6s 上限)。
    含 20 秒 memo：避免同一次開窗的 /api/stock 與 /api/analyst 重複載入同一檔。"""
    import time as _t
    key = (code, bool(live))
    hit = _DF_MEMO.get(key)
    if hit and (_t.monotonic() - hit[0]) < _DF_TTL:
        return hit[1]
    df = scan._read_cache(code)
    # 快取過時檢查：非精選股每日不刷新→快取可能停在數週前的舊價(盟立/微星實測停在06-11)。
    # 最後一筆若距今 > 5 天，視為過時、丟棄改抓官方最新(twstock)，避免顯示舊價。
    if df is not None and len(df):
        try:
            import pandas as _pd
            stale = (_pd.Timestamp.now().normalize() - df.index[-1].normalize()).days > 5
        except Exception:
            stale = False
        if stale:
            df = None
    if df is None:
        # 抓官方最新(_bulk_yf 日線已改走 twstock 官方)，包在硬性 timeout 內避免慢網卡死
        def _fetch():
            got = scan._bulk_yf([code], ".TW")
            if code not in got:
                got = scan._bulk_yf([code], ".TWO")
            return got.get(code)
        df = _run_bounded(_fetch, timeout=yf_timeout)
    if df is None:
        return None
    if live:
        bag = {code: df}

        def _rt():
            scan.apply_realtime(bag)      # 同日覆蓋 / 跨日新增(內建交易時段防呆；走 twstock 即時網路)
            return bag[code]
        df = _run_bounded(_rt, timeout=6.0, default=df)   # 即時價也設上限，抓不到就用原日線
    if df is not None:
        _DF_MEMO[key] = (_t.monotonic(), df)
        if len(_DF_MEMO) > 64:            # 上限保護，清最舊
            for k in sorted(_DF_MEMO, key=lambda k: _DF_MEMO[k][0])[:32]:
                _DF_MEMO.pop(k, None)
    return df


# ── 籌碼面 merge(單檔；缺資料優雅降級 None) ──────────────────────────────────
def _merge_chips(code: str, live: bool) -> dict:
    """三大法人：對齊 build_state 的合併邏輯(chip_confirm 同式)。"""
    out = {"foreign_net": None, "trust_net": None, "instinv_net": None,
           "consec_buy_days": None, "trust_consec_days": None, "chip_confirm": None}
    if scan.chips is None:
        return out
    try:
        # offline=True：只讀本地快取(背景掃描已刷)，絕不即時探網 → 互動查詢秒回不卡；缺→None
        rec = scan.chips.load_chips([code], days=scan.CHIP_DAYS, offline=True).get(code)
    except Exception:
        rec = None
    if rec:
        ft_buy = (rec["net_sum_n"] > 0) or (rec["consec_buy_days"] >= scan.CHIP_CONSEC_MIN)
        out.update({
            "foreign_net": rec["foreign_net"], "trust_net": rec["trust_net"],
            "instinv_net": rec["instinv_net"], "consec_buy_days": rec["consec_buy_days"],
            "trust_consec_days": rec.get("trust_consec_days"), "chip_confirm": bool(ft_buy),
        })
    return out


def _merge_margin(code: str, vol_lots: int | None) -> dict:
    """融資融券/當沖：day_trade_pct 同 build_state 的算法(當沖張/當根張×100，>100 或缺量→None)。"""
    out = {"margin_balance": None, "margin_chg": None, "short_balance": None,
           "short_margin_ratio": None, "day_trade_pct": None}
    if scan.margin_mod is None:
        return out
    try:
        m = scan.margin_mod.load_margin([code], offline=True).get(code)   # 只讀快取，不探網
    except Exception:
        m = None
    if m:
        vl = vol_lots or 0
        lots = m.get("day_trade_lots")
        if lots is None:
            dtp = None
        elif vl > 0:
            _raw = (lots / vl * 100) if lots else 0.0
            dtp = round(_raw, 1) if _raw <= 100 else None
        else:
            dtp = None
        out.update({"margin_balance": m["margin_balance"], "margin_chg": m["margin_chg"],
                    "short_balance": m["short_balance"],
                    "short_margin_ratio": m["short_margin_ratio"], "day_trade_pct": dtp})
    return out


def _merge_fundamentals(code: str) -> dict:
    """基本面/估值(離線只讀快取；缺→None 優雅降級)：PE/PB/殖利率/EPS/毛利/營收YoY/股利。"""
    out = {"pe": None, "pb": None, "dividend_yield": None,
           "eps_q": None, "eps_ttm": None, "eps_yoy": None,
           "gross_margin": None, "op_margin": None,
           "rev_yoy": None, "rev_mom": None,
           "cash_div": None, "stock_div": None, "ex_date": None}
    try:
        import fundamentals as _fd
    except Exception:
        return out
    f = _fd.load_fundamentals(code, offline=True)
    # 首次查詢該股：本地無財報快取 → 有界線上抓一次(≤7s)，之後快取秒回；抓不到就降級
    if not f.get("has_financials"):
        _run_bounded(lambda: _fd.fetch_stock_fundamentals(code), timeout=7.0, default=None)
        f = _fd.load_fundamentals(code, offline=True)
    for k in out:
        if f.get(k) is not None:
            out[k] = f[k]
    return out


def _merge_tdcc(code: str) -> dict:
    """集保戶數(週更新)：散戶流出旗標 + 週變化%。"""
    out = {"small_count": None, "small_count_chg": None, "small_chg_pct": None,
           "retail_exit": None, "retail_surge": None}
    if scan.tdcc_mod is None:
        return out
    try:
        rec = scan.tdcc_mod.load_tdcc([code], offline=True).get(code)   # 只讀快取，不探 22 日網
    except Exception:
        rec = None
    if rec:
        out.update({"small_count": rec["small_count"], "small_count_chg": rec.get("small_chg"),
                    "small_chg_pct": rec.get("small_chg_pct"),
                    "retail_exit": rec.get("retail_exit"), "retail_surge": rec.get("retail_surge")})
    return out


# ── 完整指標補齊(ma20/ma60/macd/atr；adx/%b 已在 core) ───────────────────────
def _full_indicators(df, live: bool) -> dict:
    """在與 _analyse_core 同一個『已收盤』序列上補算 ma20/ma60/macd/atr，重用 scan 的 helper。
    前端契約：macd 與 macd_hist 都給純量；完整字典另存 macd_detail(前端可忽略)。"""
    full = scan._lower(df.tail(180).reset_index(drop=True))
    closed = full.iloc[:-1] if (live and len(full) > 22) else full
    ma = scan.calc_ma(closed, periods=[20, 60])
    macd = scan.calc_macd(closed)
    atr = scan._atr_last(closed, scan.CHAND_LEN)
    m20, m60 = ma["ma"].get(20), ma["ma"].get(60)
    m_line, m_hist = macd.get("macd"), macd.get("histogram")
    return {
        "ma20": round(m20, 2) if m20 is not None else None,
        "ma60": round(m60, 2) if m60 is not None else None,
        "atr": round(atr, 2) if atr is not None else None,
        "macd": round(m_line, 3) if m_line is not None else None,       # MACD 線(純量)
        "macd_hist": round(m_hist, 3) if m_hist is not None else None,  # 柱狀(純量)
        "macd_detail": macd,     # {macd, signal_line, histogram, crossover, trend}
    }


def _extended_indicators(df, live: bool) -> dict:
    """擴充指標(OBV/DMI/威廉/CCI/寶塔線) + 週K/月K，對齊三竹多指標。缺→None 不阻塞。"""
    out = {"obv_trend": None, "plus_di": None, "minus_di": None, "dmi_signal": None,
           "williams_r": None, "williams_zone": None, "cci": None, "cci_zone": None,
           "tower": None, "tower_signal": None, "ohlc_w": None, "ohlc_m": None}
    try:
        import indicators as _ind
    except Exception:
        return out
    d = df.copy()
    if live and len(d) > 22:
        d = d.iloc[:-1]          # 盤中丟未收盤 forming K
    d = d.tail(300)
    try:
        out["obv_trend"] = _ind.calc_obv(d).get("trend")
        dmi = _ind.calc_dmi(d)
        out.update({"plus_di": dmi.get("plus_di"), "minus_di": dmi.get("minus_di"),
                    "dmi_signal": dmi.get("signal")})
        wr = _ind.calc_williams_r(d)
        out.update({"williams_r": wr.get("williams_r"), "williams_zone": wr.get("zone")})
        cci = _ind.calc_cci(d)
        out.update({"cci": cci.get("cci"), "cci_zone": cci.get("zone")})
        tw = _ind.calc_tower(d)
        out.update({"tower": tw.get("tower"), "tower_signal": tw.get("signal")})
        out["ohlc_w"] = _ind.resample_ohlc(df, "W", 40) or None
        out["ohlc_m"] = _ind.resample_ohlc(df, "M", 40) or None
    except Exception:
        pass
    return out


# ── 對外主函式 ───────────────────────────────────────────────────────────────
def analyze_stock(code: str, live: bool = False) -> dict:
    """單一個股完整分析。成功回 {ok:True, ...卡片欄位..., 籌碼, 完整指標}；失敗回 {ok:False, error}。
    任意上市櫃代號或名稱皆可查(不限掃描宇宙)。live=True 用證交所即時價覆蓋最後一根。"""
    raw = (code or "").strip()
    if not raw:
        return {"ok": False, "error": "empty query"}

    resolved = _resolve_code(raw)
    if not resolved:
        return {"ok": False, "error": f"查無此股：{raw}"}

    df = _load_df(resolved, live)
    if df is None:
        return {"ok": False, "error": f"{resolved} 無價格資料(未在快取且即時抓取失敗)"}

    # drop_last=live：盤中即時覆蓋的 forming K 不進訊號/指標(與 scan 收盤確認一致)
    core = scan._analyse_core(resolved, df, drop_last=live)
    if not core:
        return {"ok": False, "error": f"{resolved} 資料不足(需 ≥22 根)無法分析"}

    name, industry = _meta(resolved)
    chips = _merge_chips(resolved, live)
    margin = _merge_margin(resolved, core.get("vol_lots"))
    tdcc = _merge_tdcc(resolved)
    fund = _merge_fundamentals(resolved)
    ind = _full_indicators(df, live)
    ext = _extended_indicators(df, live)

    result = {
        "ok": True,
        "live": bool(live),
        # ── 卡片核心欄位(對齊 scan._card / _sig，前端 popover 可直接重用) ──
        "code": resolved, "name": name, "industry": industry,
        **fund,
        "price": core["price"], "chg": core["chg"], "rsi": core["rsi"],
        "score": core["score"], "st": core["st"],
        "spark": core["spark"], "ohlc": core.get("ohlc"),
        # side 與 signal 同值(前端契約：side('long'/'short'或無)或 signal)
        "signal": core["signal"], "side": core["signal"], "reason": core["reason"],
        "stop": core["stop"], "tp1": core["tp1"], "tp2": core["tp2"],
        # ── 四維籌碼(缺資料 None)；pool_pass 為選股池濾網旗標 ──
        "pool_pass": core.get("pool_pass"),
        **chips, **margin, **tdcc,
        # ── 完整指標(pct_b 為前端契約鍵名，percent_b 保留為別名) ──
        "adx": core["adx"], "pct_b": core["percent_b"], "percent_b": core["percent_b"],
        **ind, **ext,
        # 額外參考(不破壞卡片；前端可忽略)
        "mom5": core.get("mom5"), "relvol": core.get("relvol"),
        "above20": core.get("above20"), "above60": core.get("above60"),
        "macd_trend": core.get("macd_trend"), "firm": core.get("firm"),
        "kd_k": core.get("kd_k"), "kd_d": core.get("kd_d"),
        "kd_golden": core.get("kd_golden"), "kd_death": core.get("kd_death"),
        "bald_red_k": core.get("bald_red_k"),
    }
    # 個股健診（四面向連續計分；用上面已合併的 技術+籌碼+基本面 統一 dict）
    try:
        import health as _health
        result["health"] = _health.compute_health(result)
    except Exception:
        result["health"] = None
    return result


# ── CLI 自測 ─────────────────────────────────────────────────────────────────
def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    live = "--live" in sys.argv
    if not args:
        print("用法：python query.py <代號或名稱> [--live]")
        return
    q = args[0]
    res = analyze_stock(q, live=live)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
