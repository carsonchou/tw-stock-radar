"""
notify.py — 多管道推播通知系統
支援 ntfy.sh 與 LINE Notify，可單獨或同時推送。
"""

from __future__ import annotations

import os
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 常數
# ──────────────────────────────────────────────
NTFY_BASE = "https://ntfy.sh"
LINE_NOTIFY_URL = "https://notify-api.line.me/api/notify"

PRIORITY_MAP = {
    "default": "default",
    "high": "high",
    "urgent": "urgent",
    "max": "urgent",
}

YOUTUBE_CTA = "更多分析：量化阿森 YouTube"
SIGNAL_ICON = {"buy": "🟢", "sell": "🔴", "neutral": "🟡"}


# ──────────────────────────────────────────────
# 1. ntfy.sh
# ──────────────────────────────────────────────
def send_ntfy(
    message: str,
    title: str = "",
    topic: str | None = None,
    priority: str = "default",
    timeout: int = 10,
) -> bool:
    """
    透過 ntfy.sh 推送通知。

    Parameters
    ----------
    message  : 通知內文
    title    : 通知標題（可選）
    topic    : ntfy topic；若省略則從 .env NTFY_TOPIC 讀取
    priority : "default" | "high" | "urgent"
    timeout  : HTTP 逾時秒數

    Returns
    -------
    bool：推送成功為 True，失敗為 False
    """
    resolved_topic = topic or os.getenv("NTFY_TOPIC", "").strip()
    if not resolved_topic:
        print("[notify] ⚠️  NTFY_TOPIC 未設定，略過 ntfy 推播")
        return False

    resolved_priority = PRIORITY_MAP.get(priority.lower(), "default")
    url = f"{NTFY_BASE}/{resolved_topic}"

    headers: dict[str, str] = {
        "Content-Type": "text/plain; charset=utf-8",
        "X-Priority": resolved_priority,
    }
    if title:
        headers["X-Title"] = title

    try:
        resp = httpx.post(url, content=message.encode("utf-8"), headers=headers, timeout=timeout)
        resp.raise_for_status()
        print(f"[notify] ✅ ntfy → {url} ({resp.status_code})")
        return True
    except httpx.HTTPStatusError as exc:
        print(f"[notify] ❌ ntfy HTTP 錯誤 {exc.response.status_code}: {exc}")
        return False
    except httpx.RequestError as exc:
        print(f"[notify] ❌ ntfy 連線錯誤: {exc}")
        return False


# ──────────────────────────────────────────────
# 2. LINE Notify
# ──────────────────────────────────────────────
def send_line(
    message: str,
    token: str | None = None,
    timeout: int = 10,
) -> bool:
    """
    透過 LINE Notify 推送訊息。

    Parameters
    ----------
    message : 推播訊息內文（LINE 會自動前置換行）
    token   : LINE Notify token；若省略則從 .env LINE_NOTIFY_TOKEN 讀取
    timeout : HTTP 逾時秒數

    Returns
    -------
    bool：推送成功為 True，失敗或 token 未設定為 False
    """
    resolved_token = token or os.getenv("LINE_NOTIFY_TOKEN", "").strip()
    if not resolved_token:
        print("[notify] ℹ️  LINE_NOTIFY_TOKEN 未設定，略過 LINE 推播")
        return False

    headers = {"Authorization": f"Bearer {resolved_token}"}
    data = {"message": f"\n{message}"}

    try:
        resp = httpx.post(LINE_NOTIFY_URL, headers=headers, data=data, timeout=timeout)
        resp.raise_for_status()
        print(f"[notify] ✅ LINE Notify ({resp.status_code})")
        return True
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            print("[notify] ❌ LINE Notify token 無效或已過期（401）")
        else:
            print(f"[notify] ❌ LINE Notify HTTP 錯誤 {status}: {exc}")
        return False
    except httpx.RequestError as exc:
        print(f"[notify] ❌ LINE Notify 連線錯誤: {exc}")
        return False


# ──────────────────────────────────────────────
# 3. broadcast
# ──────────────────────────────────────────────
def broadcast(
    message: str,
    title: str = "",
    priority: str = "default",
) -> dict[str, bool]:
    """
    同時推播到 ntfy.sh 與 LINE Notify（有設定才送）。

    Returns
    -------
    dict with keys "ntfy" and "line", value True = 成功
    """
    ntfy_ok = send_ntfy(message, title=title, priority=priority)
    line_ok = send_line(message)
    return {"ntfy": ntfy_ok, "line": line_ok}


# ──────────────────────────────────────────────
# 4. format_signal_message
# ──────────────────────────────────────────────
def format_signal_message(signals: list[dict]) -> str:
    """
    將選股訊號列表格式化為可讀推播訊息。

    每個 signal dict 欄位：
        ticker      : str  — 股票代號，例如 "2330"
        name        : str  — 股票名稱，例如 "台積電"
        price       : float — 當日收盤價
        signal_type : str  — "buy" | "sell" | "neutral"
        reason      : str  — 理由說明

    範例輸出：
        📊 今日量化選股訊號
        ========================
        🟢 2330 台積電 $1,050
           訊號：SuperTrend 翻多
           理由：三線同向向上，RSI 55 健康區
        ...
        ========================
        更多分析：量化阿森 YouTube
    """
    if not signals:
        return "📊 今日無量化訊號"

    lines: list[str] = [
        "📊 今日量化選股訊號",
        "========================",
    ]

    for sig in signals:
        ticker = sig.get("ticker", "")
        name = sig.get("name", "")
        price = sig.get("price", 0)
        signal_type = str(sig.get("signal_type", "neutral")).lower()
        reason = sig.get("reason", "")

        # 訊號文字映射
        signal_label_map = {
            "buy": "翻多",
            "sell": "翻空",
            "neutral": "觀望",
        }
        icon = SIGNAL_ICON.get(signal_type, "🟡")
        signal_label = sig.get("signal_label") or signal_label_map.get(signal_type, signal_type)

        price_str = f"${price:,.0f}" if isinstance(price, (int, float)) else str(price)

        lines.append(f"{icon} {ticker} {name} {price_str}")
        lines.append(f"   訊號：{signal_label}")
        if reason:
            lines.append(f"   理由：{reason}")
        lines.append("")  # 空行分隔

    # 移除最後一個多餘空行
    if lines and lines[-1] == "":
        lines.pop()

    lines.append("========================")
    lines.append(YOUTUBE_CTA)

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 測試入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 50)
    print("notify.py 自我測試")
    print("=" * 50)

    # 範例訊號
    test_signals = [
        {
            "ticker": "2330",
            "name": "台積電",
            "price": 1050,
            "signal_type": "buy",
            "signal_label": "SuperTrend 翻多",
            "reason": "三線同向向上，RSI 55 健康區",
        },
        {
            "ticker": "2454",
            "name": "聯發科",
            "price": 820,
            "signal_type": "sell",
            "signal_label": "跌破 20MA",
            "reason": "量縮跌破均線，MACD 死叉確認",
        },
        {
            "ticker": "6505",
            "name": "台塑化",
            "price": 73.5,
            "signal_type": "neutral",
            "signal_label": "震盪觀望",
            "reason": "布林通道收窄，等待方向確認",
        },
    ]

    # 格式化訊息
    msg = format_signal_message(test_signals)
    print("\n[format_signal_message 輸出]")
    print(msg)

    print("\n[broadcast 測試]（需 .env 有設定 NTFY_TOPIC / LINE_NOTIFY_TOKEN）")
    result = broadcast(msg, title="量化阿森｜今日訊號", priority="high")
    print(f"推播結果: {result}")

    print("\n[空訊號測試]")
    print(format_signal_message([]))
