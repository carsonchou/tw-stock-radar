# -*- coding: utf-8 -*-
"""
eod.py — 數據獵手「盤後一鍵管線」(R7)

把整套盤後流程串成一個指令：收盤後跑一次，資料→掃描→貼文一條龍。
純編排(orchestration)：只 import 既有模組複用其入口，不重寫任何邏輯、不改任何其他檔。

依序(每步印進度；單步失敗只記錄、續跑不中斷)：
  1. 刷新快取         scan.freshen_cache(精選宇宙)   ← 近期日線併進 twdata/cache
  2. 三大法人籌碼     chips.backfill()               ← 增量抓當日 T86/TPEX
  3. 融資融券/當沖    margin.backfill()              ← 增量抓當日 MI_MARGN/TWTB4U
  4. 集保戶數(週)     tdcc.load_tdcc()               ← 當週有就跳過(週資料)
  5. 掃描出 state     scan.run_once(cache_only=True) ← 產 state.json(預設不推)
  6. 產貼文           daily_post.run()               ← caption + 快照(可 --no-post 跳過)

最後印總結：各步 OK/跳過/失敗、市場溫度、產出檔路徑。

用法：
  python eod.py                 # 全跑(不推播、產貼文)
  python eod.py --no-post       # 不產貼文(只到 state.json)
  python eod.py --date 20260630 # 指定 as-of 日(影響 chips/margin 回補的截止日)
  python eod.py --push          # 掃描後推 ntfy(預設不推)

健壯：某模組不可用或某步網路失敗 → 該步跳過、其他照跑，最後如實標示。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_FILE = HERE / "state.json"
POSTS_DIR = HERE / "posts"


def _imp(name: str):
    """安全 import 既有模組；缺任何相依 → 回 None，讓對應步驟優雅跳過。"""
    try:
        return __import__(name)
    except Exception as e:
        print(f"[eod] 模組 {name} 不可用（該步將跳過）：{type(e).__name__}: {e}")
        return None


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y%m%d").date()
    except ValueError:
        print(f"[eod] --date 格式錯（需 YYYYMMDD）：{s}，忽略。")
        return None


def run_eod(no_post: bool = False, as_of: date | None = None, push: bool = False) -> dict:
    t0 = time.time()
    results: list[dict] = []      # 每步：{step, status(ok/skip/fail), detail}

    def step(name: str, fn):
        print(f"\n[eod] ▶ {name} …")
        s0 = time.time()
        try:
            detail = fn()
            status = "ok"
        except Exception as e:
            detail = f"{type(e).__name__}: {e}"
            status = "fail"
            print(f"[eod] ✗ {name} 失敗（續跑）：{detail}")
        else:
            print(f"[eod] ✓ {name}（{time.time()-s0:.1f}s）：{detail}")
        results.append({"step": name, "status": status, "detail": str(detail)})

    scan = _imp("scan")
    chips = _imp("chips")
    margin = _imp("margin")
    tdcc = _imp("tdcc")
    daily_post = _imp("daily_post")

    # ── 1. 刷新快取 ──────────────────────────────────────────────────────
    def _freshen():
        if scan is None:
            raise RuntimeError("scan 模組不可用")
        from universe import all_codes
        rows = all_codes()
        n = scan.freshen_cache(rows)
        return f"更新 {n}/{len(rows)} 檔日線快取"
    step("1/6 刷新快取", _freshen)

    # ── 2. 三大法人籌碼 ─────────────────────────────────────────────────
    def _chips():
        if chips is None:
            raise RuntimeError("chips 模組不可用")
        days = chips.backfill(days=getattr(chips, "DEFAULT_DAYS", 5), end=as_of)
        return f"備妥 {len(days)} 個交易日籌碼" + (f"（最新 {days[0].isoformat()}）" if days else "")
    step("2/6 三大法人籌碼", _chips)

    # ── 3. 融資融券/當沖 ────────────────────────────────────────────────
    def _margin():
        if margin is None:
            raise RuntimeError("margin 模組不可用")
        days = margin.backfill(days=getattr(margin, "DEFAULT_DAYS", 5), end=as_of)
        return f"備妥 {len(days)} 個交易日融資券當沖" + (f"（最新 {days[0].isoformat()}）" if days else "")
    step("3/6 融資融券/當沖", _margin)

    # ── 4. 集保戶數(週) ─────────────────────────────────────────────────
    def _tdcc():
        if tdcc is None:
            raise RuntimeError("tdcc 模組不可用")
        data = tdcc.load_tdcc()          # 精選宇宙、當週(已快取則跳過)
        enc = next(iter(data.values())).get("enc_date") if data else None
        return f"取得 {len(data)} 檔集保戶數" + (f"（encDate {enc}）" if enc else "（無有效週資料）")
    step("4/6 集保戶數(週)", _tdcc)

    # ── 4b. 基本面預抓(估值全市場 + 輪替刷財報，讓常見股秒回) ─────────────
    def _fund():
        try:
            import fundamentals as _fd
            import universe as _u
            codes = [c for c, *_ in _u.all_codes()]
            r = _fd.prefetch(codes, max_financials=30)
            return f"估值 {r['valuation']} 檔｜財報輪刷 {r['financials_refreshed']}/{r['financials_stale']}"
        except Exception as e:
            return f"略過（{type(e).__name__}: {e}）"
    step("4b/6 基本面預抓", _fund)

    # ── 5. 掃描出 state ─────────────────────────────────────────────────
    temp_txt = "?"

    def _scan():
        nonlocal temp_txt
        if scan is None:
            raise RuntimeError("scan 模組不可用")
        st = scan.run_once(push=push, cache_only=True)
        if st.get("ok"):
            g = st["gauge"]
            temp_txt = f"{g['temperature']}（{g['label']}）"
            return (f"溫度 {temp_txt}｜漲{g['adv']}/跌{g['dec']}"
                    f"｜做多{len(st['signals']['long'])} 做空{len(st['signals']['short'])}"
                    f"｜{'已推播' if push else '未推'}")
        return f"state 非正常：{st.get('error')}"
    step("5/6 掃描出 state", _scan)

    # ── 5b. 交易專區(全市場當沖/短線/長線選股) ───────────────────────────
    def _zones():
        try:
            import zones as _z
            z = _z.build_zones(full=True, use_cache_only=True)
            return (f"全市場 {z['universe_n']} 檔｜當沖{len(z['daytrade']['cands'])}"
                    f"/短線{len(z['swing']['cands'])}/長線{len(z['longterm']['cands'])}")
        except Exception as e:
            return f"略過（{type(e).__name__}: {e}）"
    step("5b/6 交易專區", _zones)

    # ── 6. 產貼文 ───────────────────────────────────────────────────────
    post_dir = None
    if no_post:
        print("\n[eod] ▶ 6/6 產貼文 … 跳過（--no-post）")
        results.append({"step": "6/6 產貼文", "status": "skip", "detail": "--no-post"})
    else:
        def _post():
            nonlocal post_dir
            if daily_post is None:
                raise RuntimeError("daily_post 模組不可用")
            post_dir = daily_post.run(do_scan=False)   # 用剛產的 state.json
            return f"貼文素材 → {post_dir}"
        step("6/6 產貼文", _post)

    # ── 總結 ────────────────────────────────────────────────────────────
    ok = sum(1 for r in results if r["status"] == "ok")
    skip = sum(1 for r in results if r["status"] == "skip")
    fail = sum(1 for r in results if r["status"] == "fail")
    print("\n" + "=" * 56)
    print(f"[eod] 盤後管線完成：OK {ok}／跳過 {skip}／失敗 {fail}（總耗時 {time.time()-t0:.1f}s）")
    for r in results:
        icon = {"ok": "✓", "skip": "—", "fail": "✗"}[r["status"]]
        print(f"   {icon} {r['step']}：{r['detail']}")
    print(f"[eod] 市場溫度：{temp_txt}")
    print(f"[eod] 產出：{STATE_FILE.name}" + (f"、貼文 {post_dir}" if post_dir else ""))
    print("=" * 56)
    return {"results": results, "ok": ok, "skip": skip, "fail": fail,
            "temperature": temp_txt, "state": str(STATE_FILE), "post_dir": str(post_dir) if post_dir else None}


def main():
    ap = argparse.ArgumentParser(description="數據獵手盤後一鍵管線")
    ap.add_argument("--no-post", action="store_true", help="不產貼文(只到 state.json)")
    ap.add_argument("--date", type=str, default=None, help="as-of 日 YYYYMMDD(影響 chips/margin 回補截止)")
    ap.add_argument("--push", action="store_true", help="掃描後推 ntfy(預設不推)")
    args = ap.parse_args()
    run_eod(no_post=args.no_post, as_of=_parse_date(args.date), push=args.push)


if __name__ == "__main__":
    main()
