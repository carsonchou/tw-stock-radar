# -*- coding: utf-8 -*-
"""
daily_post.py — 數據獵手「一鍵今日貼文」(R5，純本地免外網)

把當日 state.json 變成 IG/YT 可直接發的素材：繁中模板文案(免 LLM、離線) + 看板快照圖。
走「量化阿森 Carson Quant」頻道「數字戳破直覺」風格：先用一個反差數字當鉤子。

流程：
  1. 讀 data_hunter/state.json(沒有或 --scan 就先 scan.run_once(push=False, cache_only=True) 產一份)。
  2. 模板生成 caption_ig.txt(IG 短文案≤2200字) + caption_yt.txt(YT 描述長版)。
  3. 起臨時本地 server(挑空埠)→ 用 snapshot.py 對 dashboard.html?snapshot=<主題> 截 IG 直式
     1080×1350(board/chips/track/flow)→ 關 server。某主題失敗就跳過。
  4. 輸出到 data_hunter/posts/<YYYY-MM-DD>/：caption_ig.txt、caption_yt.txt、
     board.png、chips.png、track.png、flow.png(有幾張算幾張)。

用法：
  python daily_post.py                      # 用現有 state.json
  python daily_post.py --scan               # 先掃一次(cache)再產
  python daily_post.py --themes board,chips # 只出指定主題圖
  python daily_post.py --mock               # 用 state.sample.json 渲染快照(測試)
  python daily_post.py --no-shots           # 只出文案不截圖
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

STATE_FILE = HERE / "state.json"
POSTS_DIR = HERE / "posts"

# 主題 → dashboard.html?snapshot=<query>。board 用既有 ?snapshot=1(必定可截)；
# chips/track/flow 對應前端新增分頁(還沒好就會截到預設版或失敗→跳過，優雅降級)。
THEME_QUERY = {"board": "1", "chips": "chips", "track": "track", "flow": "flow"}
DEFAULT_THEMES = ["board", "chips", "track", "flow"]

MAX_IG_LEN = 2200
HASHTAGS_BASE = ["#台股", "#台股當沖", "#籌碼面", "#量化交易", "#存股"]


# ── 小工具 ──────────────────────────────────────────────────────────────────
def _arrow(chg) -> str:
    """漲跌箭頭(台股紅漲綠跌，文字用 ▲/▼)。None → '—'。"""
    if chg is None:
        return "—"
    if chg > 0:
        return f"▲{abs(chg):.2f}%"
    if chg < 0:
        return f"▼{abs(chg):.2f}%"
    return "▬0.00%"


def _short_reason(reason: str) -> str:
    """濃縮訊號理由：去掉『(警示/減碼)』等尾註，保留核心。"""
    if not reason:
        return ""
    for tail in ("（警示/減碼）", "(警示/減碼)", "，大盤偏空", "(供參)"):
        reason = reason.replace(tail, "")
    return reason.strip("，, ").strip()


def load_state(do_scan: bool) -> dict:
    """讀 state.json；--scan 或檔案不存在時先掃一次(cache、不推)。"""
    if do_scan or not STATE_FILE.exists():
        print("[post] 產生 state.json（scan cache 模式，不推播）…")
        try:
            import scan
            scan.run_once(push=False, cache_only=True)
        except Exception as e:
            print(f"[post] 掃描失敗（改用既有 state.json）：{type(e).__name__}: {e}")
    if not STATE_FILE.exists():
        sys.exit("[post] 找不到 state.json，且無法掃描產生。")
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


# ── 鉤子：用反差數字戳破直覺 ────────────────────────────────────────────────
def build_hook(s: dict) -> str:
    g = s.get("gauge", {})
    idx = s.get("index", {})
    temp = g.get("temperature", 50)
    label = g.get("label", "")
    adv, dec = g.get("adv", 0), g.get("dec", 0)
    breadth = g.get("breadth", 50)
    idx_chg = idx.get("chg")
    nh, nl = g.get("nh"), g.get("nl")

    # 反差優先序：指數大漲但體溫不高 → 普漲假象；體溫過熱/過冷 → 提醒；否則新高新低擴散
    if idx_chg is not None and idx_chg >= 1.2 and temp < 62:
        return (f"大盤 0050 收 {_arrow(idx_chg)}，市場溫度卻只有 {temp} 度（{label}）"
                f"——「普漲」是錯覺，真正在動的只有少數族群。")
    if temp >= 72:
        return (f"市場溫度衝到 {temp} 度（{label}），站上 20MA 的有 {breadth}%"
                f"——過熱區，追高的人先問自己停損放哪。")
    if temp <= 38:
        return (f"市場溫度只剩 {temp} 度（{label}），全市場只有 {breadth}% 站上 20MA"
                f"——多數人以為的「便宜」，其實是還在往下的刀。")
    if adv and dec and (adv / max(dec, 1)) >= 2.2 and temp < 60:
        return (f"今天漲 {adv} 跌 {dec}，看起來多方大勝，但市場溫度只有 {temp} 度（{label}）"
                f"——漲家數騙人，體溫才是真的。")
    if nh is not None and nl is not None and (nh + nl) > 0 and (nh - nl) <= 0:
        return (f"漲 {adv} 跌 {dec}，但 60 日創新高只有 {nh} 檔、創新低有 {nl} 檔"
                f"——動能其實在退，別只看紅通通的盤面。")
    return (f"台股今天 漲 {adv} 跌 {dec}，市場溫度 {temp} 度（{label}）。"
            f"數字攤開來看，才知道盤在想什麼。")


# ── 各段落 ──────────────────────────────────────────────────────────────────
def _sector_lines(s: dict) -> tuple[str, str]:
    secs = s.get("sectors", [])
    if not secs:
        return "", ""
    # state 的 sectors 是按強弱分排序；「領漲/最弱」要按當日漲跌幅排，否則會出現
    # 「領漲族群 ▼0.72%」名實不符(公開貼文會漏氣)
    by_chg = sorted(secs, key=lambda x: x.get("avg_chg") or 0, reverse=True)
    top, bot = by_chg[0], by_chg[-1]
    return (f"🔥 領漲族群：{top['name']} {_arrow(top.get('avg_chg'))}（{top.get('count',0)} 檔）",
            f"🧊 領跌族群：{bot['name']} {_arrow(bot.get('avg_chg'))}（{bot.get('count',0)} 檔）")


def _signal_lines(s: dict, per_side: int) -> list[str]:
    sig = s.get("signals", {})
    longs, shorts = sig.get("long", []), sig.get("short", [])
    out: list[str] = []
    if not longs and not shorts:
        out.append("📭 今日無明確做多/做空訊號，觀望為上。")
        return out
    if longs:
        out.append("🟢 做多亮點：")
        for x in longs[:per_side]:
            out.append(f"　• {x['code']} {x['name']} {_arrow(x.get('chg'))}"
                       f"｜{_short_reason(x.get('reason',''))}")
    if shorts:
        out.append("🔴 做空／減碼警示：")
        for x in shorts[:per_side]:
            out.append(f"　• {x['code']} {x['name']} {_arrow(x.get('chg'))}"
                       f"｜{_short_reason(x.get('reason',''))}")
    return out


_SIDE_ZH = {"foreign": "外資", "trust": "投信"}


def _chip_lines(s: dict) -> list[str]:
    ch = s.get("chips") or {}
    out: list[str] = []
    ft = ch.get("foreign_top") or []
    tt = ch.get("trust_top") or []
    ct = ch.get("consec_top") or []
    tag = "（盤後T+0）" if ch.get("t_minus") == 0 else "（前一交易日T-1）"
    if ft:
        out.append(f"🏦 外資買超王：{ft[0]['name']} {ft[0]['net']:+,} 張")
    if tt:
        out.append(f"🏛 投信買超王：{tt[0]['name']} {tt[0]['net']:+,} 張")
    if ct:
        c0 = ct[0]
        out.append(f"🔗 連續買超：{c0['name']} 連 {c0['consec']} 日"
                   f"（{_SIDE_ZH.get(c0.get('side'),'法人')}）")
    if out:
        out.insert(0, f"📌 三大法人動向{tag}：")
    return out


def _margin_lines(s: dict) -> list[str]:
    ch = s.get("chips") or {}
    mt = ch.get("margin_top") or []
    if not mt:
        return []
    m0 = mt[0]
    smr = m0.get("short_margin_ratio")
    dtp = m0.get("day_trade_pct")
    parts = [f"⚔ 券資比最高：{m0['name']} {smr}%"] if smr is not None else []
    if dtp is not None:
        parts.append(f"當沖比 {dtp}%")
    return ["　".join(parts)] if parts else []


def _track_line(s: dict) -> str:
    tk = s.get("track") or {}
    if tk.get("n_closed", 0) > 0:
        return (f"📈 近期已平倉 {tk['n_closed']} 筆，勝率 {tk.get('win_rate',0)*100:.0f}%"
                f"，平均 {tk.get('avg_r',0):+.2f}R（程式客觀回測，非喊單）")
    return ""


def _hashtags(s: dict) -> str:
    tags = list(HASHTAGS_BASE)
    names = {x.get("code"): x.get("name") for side in ("long", "short")
             for x in s.get("signals", {}).get(side, [])}
    if "2330" in names:
        tags.append("#台積電")
    if any((sec.get("name") == "半導體") for sec in s.get("sectors", [])[:1]):
        tags.append("#半導體")
    if (s.get("chips") or {}).get("foreign_top"):
        tags.append("#外資")
    # 去重保序、最多 8 個
    seen, uniq = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); uniq.append(t)
    return " ".join(uniq[:8])


# ── IG / YT 文案組裝 ────────────────────────────────────────────────────────
def build_ig(s: dict) -> str:
    g = s.get("gauge", {})
    idx = s.get("index", {})
    d = s.get("date", str(date.today()))
    top_line, bot_line = _sector_lines(s)
    parts: list[str] = [
        build_hook(s), "",
        f"📅 {d}　台股收盤掃描",
        f"🌡 市場溫度 {g.get('temperature','?')}（{g.get('label','')}）"
        f"｜大盤0050 {_arrow(idx.get('chg'))}",
        f"📊 漲 {g.get('adv',0)}／跌 {g.get('dec',0)}／平 {g.get('flat',0)}"
        f"｜站上20MA {g.get('breadth','?')}%",
    ]
    if top_line:
        parts += ["", top_line, bot_line]
    parts += [""] + _signal_lines(s, per_side=3)
    chips = _chip_lines(s)
    if chips:
        parts += [""] + chips
    margin = _margin_lines(s)
    if margin:
        parts += margin
    tl = _track_line(s)
    if tl:
        parts += ["", tl]
    parts += [
        "",
        "⚠ 以上為程式量化掃描結果，非投資建議，據此進出風險自負。",
        "👉 量化阿森 · 每天掃給你看，追蹤不錯過",
        "", _hashtags(s),
    ]
    text = "\n".join(parts)
    if len(text) > MAX_IG_LEN:                 # 保險：超長先砍訊號段(留鉤子+溫度+CTA)
        text = text[:MAX_IG_LEN - 20].rstrip() + "\n…（完整見圖）"
    return text


def build_yt(s: dict) -> str:
    g = s.get("gauge", {})
    idx = s.get("index", {})
    d = s.get("date", str(date.today()))
    L: list[str] = [
        f"【量化阿森｜台股數據獵手】{d} 收盤全市場掃描",
        "",
        build_hook(s),
        "",
        "──────────",
        "🌡 市場溫度計",
        f"　溫度 {g.get('temperature','?')} 度（{g.get('label','')}）"
        f"｜平均RSI {g.get('avg_rsi','?')}｜站上20MA {g.get('breadth','?')}%",
        f"　漲 {g.get('adv',0)}／跌 {g.get('dec',0)}／平 {g.get('flat',0)}"
        f"｜漲跌比(ADR) {g.get('adr','?')}｜60日新高{g.get('nh','?')}/新低{g.get('nl','?')}",
        f"　大盤 0050 {_arrow(idx.get('chg'))}，趨勢 {idx.get('trend','?')}"
        f"{'（站上年線）' if idx.get('above_yearline') else ''}",
        "",
        "🔄 板塊輪動",
    ]
    for sec in s.get("sectors", [])[:5]:
        L.append(f"　{sec['name']}　{_arrow(sec.get('avg_chg'))}"
                 f"　多方 {sec.get('bull_pct','?')}%（{sec.get('count',0)} 檔）")
    L += ["", "🎯 今日訊號清單"]
    sig = s.get("signals", {})
    if sig.get("long"):
        L.append("　做多：")
        for x in sig["long"]:
            L.append(f"　　{x['code']} {x['name']} {x.get('price','')}｜{_short_reason(x.get('reason',''))}"
                     f"｜停損 {x.get('stop','—')} / TP1 {x.get('tp1','—')}")
    if sig.get("short"):
        L.append("　做空／減碼：")
        for x in sig["short"]:
            L.append(f"　　{x['code']} {x['name']} {x.get('price','')}｜{_short_reason(x.get('reason',''))}")
    if not sig.get("long") and not sig.get("short"):
        L.append("　今日無明確訊號，續抱觀望。")
    chips = _chip_lines(s)
    if chips:
        L += [""] + chips
    margin = _margin_lines(s)
    if margin:
        L += margin
    tl = _track_line(s)
    if tl:
        L += ["", tl]
    L += [
        "", "──────────",
        "📲 每天收盤，量化阿森用程式掃完整個台股，把「數字戳破直覺」的重點整理給你。",
        "訂閱 + 小鈴鐺，隔天開盤前先看懂盤在想什麼。",
        "🔗 頻道：<YOUR_CHANNEL_LINK>　｜　IG：<YOUR_IG_LINK>",
        "",
        "⚠ 免責：本內容為程式量化掃描與教學，非投資建議；投資有風險，據此進出盈虧自負。",
        "", _hashtags(s),
    ]
    return "\n".join(L)


# ── 快照(截圖) ──────────────────────────────────────────────────────────────
def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def shoot_themes(themes: list[str], out_dir: Path, mock: bool) -> list[str]:
    """起臨時本地 server，對每個主題截 IG 直式圖到 out_dir。回傳成功的主題檔名。
    健壯：找不到 Chrome / 某主題失敗都跳過，不影響文案產出。"""
    try:
        import snapshot
    except Exception as e:
        print(f"[post] 無法載入 snapshot（跳過截圖）：{type(e).__name__}: {e}")
        return []
    try:
        chrome = snapshot.find_chrome(None)     # 找不到會 SystemExit → 下面接住
    except SystemExit as e:
        print(f"[post] {e}（跳過截圖，文案照出）")
        return []

    port = _free_port()
    httpd = snapshot.start_local_server(port)
    base = f"http://127.0.0.1:{port}"
    w, h = snapshot.SIZES["ig"]
    done: list[str] = []
    try:
        if not snapshot.wait_server(base):
            print(f"[post] 本地 server 起不來（跳過截圖）：{base}")
            return []
        print(f"[post] 臨時 server → {base}，截 {w}×{h} IG 直式")
        for th in themes:
            q = THEME_QUERY.get(th, th)
            url = f"{base}/dashboard.html?snapshot={q}" + ("&mock=1" if mock else "")
            out = out_dir / f"{th}.png"
            try:
                if snapshot.shoot(chrome, url, w, h, out, budget_ms=3200):
                    done.append(f"{th}.png")
            except Exception as e:
                print(f"[post] 主題 {th} 截圖失敗（跳過）：{type(e).__name__}: {e}")
    finally:
        httpd.shutdown()
    return done


# ── 主流程 ──────────────────────────────────────────────────────────────────
def run(do_scan=False, themes=None, mock=False, no_shots=False) -> Path:
    s = load_state(do_scan)
    if not s.get("ok"):
        print(f"[post] ⚠ state 非正常（{s.get('error','?')}），仍嘗試出文案。")
    d = s.get("date", str(date.today()))
    out_dir = POSTS_DIR / d
    out_dir.mkdir(parents=True, exist_ok=True)

    ig = build_ig(s)
    yt = build_yt(s)
    (out_dir / "caption_ig.txt").write_text(ig, encoding="utf-8")
    (out_dir / "caption_yt.txt").write_text(yt, encoding="utf-8")
    print(f"[post] 文案 → {out_dir/'caption_ig.txt'}（{len(ig)} 字）、caption_yt.txt（{len(yt)} 字）")

    shots: list[str] = []
    if not no_shots:
        shots = shoot_themes(themes or DEFAULT_THEMES, out_dir, mock)
    print(f"[post] 完成：文案 2 檔＋圖 {len(shots)} 張（{', '.join(shots) if shots else '無'}）→ {out_dir}")
    return out_dir


def main():
    ap = argparse.ArgumentParser(description="數據獵手一鍵今日貼文")
    ap.add_argument("--scan", action="store_true", help="先 scan(cache) 產最新 state 再出")
    ap.add_argument("--themes", default=None, help="要截的主題(逗號分隔)：board,chips,track,flow")
    ap.add_argument("--mock", action="store_true", help="快照用 state.sample.json 渲染(測試)")
    ap.add_argument("--no-shots", action="store_true", help="只出文案不截圖")
    args = ap.parse_args()
    themes = [t.strip() for t in args.themes.split(",")] if args.themes else None
    run(do_scan=args.scan, themes=themes, mock=args.mock, no_shots=args.no_shots)


if __name__ == "__main__":
    main()
