# -*- coding: utf-8 -*-
"""
test_news.py — 個股新聞 RSS 解析(news.fetch_news)不連網測試。
monkeypatch news._get 餵假 Google News RSS，驗證 title/source/time/link 解析與「標題 - 來源」拆分。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _util  # noqa: E402
import news  # noqa: E402

FAKE_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>news</title>
<item>
  <title>台積電衝上2500元創新高 - 經濟日報</title>
  <link>https://news.example.com/a</link>
  <pubDate>Tue, 01 Jul 2026 05:53:00 GMT</pubDate>
  <source url="https://money.udn.com">經濟日報</source>
</item>
<item>
  <title>外資連三買 台積電成交爆量</title>
  <link>https://news.example.com/b</link>
  <pubDate>Wed, 02 Jul 2026 09:20:00 GMT</pubDate>
</item>
</channel></rss>"""


class TestNewsParse(unittest.TestCase):
    def setUp(self):
        self._orig = news._get
        news._get = lambda url, timeout=12: FAKE_RSS

    def tearDown(self):
        news._get = self._orig

    def test_parses_items(self):
        items = news.fetch_news("台積電", "2330", limit=8)
        self.assertEqual(len(items), 2)

    def test_source_from_element(self):
        it = news.fetch_news("台積電", "2330")[0]
        self.assertEqual(it["source"], "經濟日報")
        self.assertEqual(it["link"], "https://news.example.com/a")

    def test_source_split_from_title_when_missing(self):
        # 第二則無 <source> 且標題無「- 來源」→ source 空、標題保留
        it = news.fetch_news("台積電", "2330")[1]
        self.assertIn("外資連三買", it["title"])

    def test_title_source_split(self):
        # 第一則標題「… - 經濟日報」但有 source 元素 → 用 source 元素，標題不含來源尾
        it = news.fetch_news("台積電", "2330")[0]
        self.assertNotIn(" - 經濟日報", it["title"])

    def test_time_formatted(self):
        it = news.fetch_news("台積電", "2330")[0]
        self.assertEqual(it["time"], "07/01 05:53")

    def test_empty_on_fetch_fail(self):
        news._get = lambda url, timeout=12: None
        self.assertEqual(news.fetch_news("x", "0000"), [])


if __name__ == "__main__":
    unittest.main()
