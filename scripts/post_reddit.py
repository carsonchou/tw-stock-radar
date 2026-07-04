"""
自動發文到 Reddit r/algotrading 和 r/Python
需要在 .env 設定：REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD
"""
import os, sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

try:
    import praw
except ImportError:
    print("安裝 praw: pip install praw")
    sys.exit(1)

REPO_URL = "https://github.com/carsonchou/tw-stock-radar"

POSTS = [
    {
        "subreddit": "algotrading",
        "title": "I built a free Taiwan stock market scanner — institutional chips + 13 technicals + AI analyst signals [OC]",
        "text": f"""Been trading Taiwan stocks and frustrated by the lack of good free tools.
Built tw-stock-radar: scans all 1,800+ TWSE/TPEX stocks daily, scores them across trend/momentum/chips/fundamentals, detects buy/sell signals with ATR-based stops (TP1 +1.5R, TP2 +4.5R).

The interesting part is the chips module — Taiwan's stock exchange publishes institutional net buy/sell data (T86), margin data, and TDCC ownership distribution for free every day. The scanner ingests all of it and surfaces stocks where retail is flowing out while institutions are accumulating.

Also has a "Four AI Teachers" panel that gives per-stock deep-dives in the style of 4 different trading methodologies (trend following / chips reading / warrant flow / swing trading), powered by OpenAI-compatible models.

GitHub: {REPO_URL}
Zero API key needed for core scanner. ~100 unit tests, MIT license.""",
    },
    {
        "subreddit": "Python",
        "title": "Built a FastAPI + three.js stock scanner for Taiwan market — 1,800 stocks, 100% free open data [OC]",
        "text": f"""Side project that got out of hand: a full-stack scanner that pulls from Taiwan's free government open data APIs, scores stocks with 13 custom indicators, and renders a dark HUD dashboard with an animated three.js reactor orb.

**Tech stack**: FastAPI backend · vanilla JS + three.js frontend · Server-Sent Events for live data · stdlib unittest test suite (~100 tests, zero network)

**Interesting engineering bits**:
- Test suite uses only stdlib unittest — no pytest, no mocks, passes in < 3 seconds without network
- Backtester splits train/test by odd/even date to avoid look-ahead bias
- Signal tracker measures real post-signal win rate from live data (not from backtest)
- Chips pipeline pulls 3 separate TWSE/TDCC open data endpoints and joins them

GitHub: {REPO_URL}""",
    },
]


def main(dry_run: bool = False):
    client_id = os.environ.get("REDDIT_CLIENT_ID")
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET")
    username = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")

    if not all([client_id, client_secret, username, password]):
        print("缺少 Reddit 憑證。請在 .env 設定：")
        print("  REDDIT_CLIENT_ID=xxx")
        print("  REDDIT_CLIENT_SECRET=xxx")
        print("  REDDIT_USERNAME=你的帳號")
        print("  REDDIT_PASSWORD=你的密碼")
        sys.exit(1)

    if dry_run:
        print("[DRY RUN] 以下是要發的文：")
        for p in POSTS:
            print(f"\n--- r/{p['subreddit']} ---")
            print(f"Title: {p['title']}")
            print(f"Body:\n{p['text'][:200]}...")
        return

    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent=f"tw-stock-radar-promo/1.0 by {username}",
    )

    for p in POSTS:
        sub = reddit.subreddit(p["subreddit"])
        submission = sub.submit(title=p["title"], selftext=p["text"])
        print(f"Posted to r/{p['subreddit']}: {submission.url}")


if __name__ == "__main__":
    dry = "--dry" in sys.argv
    main(dry_run=dry)
