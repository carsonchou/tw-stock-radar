"""
自動發三則串文到 Twitter/X
需要在 .env 設定：X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_TOKEN_SECRET
"""
import os, sys, time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    import tweepy
except ImportError:
    print("安裝 tweepy: pip install tweepy")
    sys.exit(1)

REPO_URL = "https://github.com/carsonchou/tw-stock-radar"

THREAD = [
    f"""Just open-sourced tw-stock-radar — free Taiwan stock scanner for all 1,800+ listed stocks

• 13 technicals per stock, scored 0–100
• Institutional chips (TWSE T86 + TDCC retail distribution)
• AI analyst panel (4 trading methodologies)
• Dark HUD dashboard with three.js reactor orb

🔗 {REPO_URL}
Zero API key for core features. MIT license.""",

    """Taiwan's stock exchange publishes institutional buy/sell (T86), margin balance,
and TDCC ownership distribution for free every day.

tw-stock-radar ingests all of it and flags stocks where retail is flowing out
while institutions accumulate — the classic setup before a breakout.""",

    """The test suite is stdlib unittest only — no pytest, no mocks, zero network calls,
runs in < 3 seconds.

~100 tests that catch regressions in the chips pipeline immediately.""",
]


def main(dry_run: bool = False):
    api_key = os.environ.get("X_API_KEY")
    api_secret = os.environ.get("X_API_SECRET")
    access_token = os.environ.get("X_ACCESS_TOKEN")
    access_token_secret = os.environ.get("X_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_token_secret]):
        print("缺少 Twitter/X 憑證。請在 .env 設定：")
        print("  X_API_KEY=xxx")
        print("  X_API_SECRET=xxx")
        print("  X_ACCESS_TOKEN=xxx")
        print("  X_ACCESS_TOKEN_SECRET=xxx")
        sys.exit(1)

    if dry_run:
        print("[DRY RUN] 以下是要發的推文：")
        for i, tweet in enumerate(THREAD):
            print(f"\n--- 第 {i+1} 則 ---")
            print(tweet)
        return

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_token_secret,
    )

    prev_id = None
    for i, text in enumerate(THREAD):
        kwargs = {"text": text}
        if prev_id:
            kwargs["in_reply_to_tweet_id"] = prev_id

        response = client.create_tweet(**kwargs)
        prev_id = response.data["id"]
        print(f"Tweet {i+1}/{len(THREAD)} posted: https://x.com/i/web/status/{prev_id}")
        if i < len(THREAD) - 1:
            time.sleep(3)  # 避免 rate limit


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    main(dry_run=dry)
