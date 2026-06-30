"""Unit tests for fetch.py: sanitising, link safety, date parsing and the
time-window filtering. No network -- feedparser and the HTTP fetch are stubbed.
"""
import sys
import time
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch

NOW = datetime(2026, 6, 28, 7, 0, tzinfo=timezone.utc)
DEFAULTS = {"window_hours": 24, "max_items": 50, "order": "feed", "summary_word_limit": 50}


def struct(year, month, day, hour=0, minute=0, second=0):
    return time.struct_time((year, month, day, hour, minute, second, 0, 0, 0))


def entry(title="Title", link="https://example.com/a", summary="x", published_parsed=None):
    ns = types.SimpleNamespace(title=title, link=link, summary=summary)
    if published_parsed is not None:
        ns.published_parsed = published_parsed
    return ns


def run_feed(entries, *, now=NOW, defaults=None, category="News", source=None):
    """Run fetch.fetch_feed against in-memory entries, stubbing the network."""
    defaults = defaults or DEFAULTS
    source = source or {"source": "Test", "url": "https://example.com/feed"}
    orig_bytes, orig_fp = fetch.fetch_bytes, fetch.feedparser
    fetch.fetch_bytes = lambda url: b""
    fetch.feedparser = types.SimpleNamespace(
        parse=lambda raw: types.SimpleNamespace(entries=entries)
    )
    try:
        return fetch.fetch_feed(source, defaults, now, category)
    finally:
        fetch.fetch_bytes, fetch.feedparser = orig_bytes, orig_fp


class SanitiseTests(unittest.TestCase):
    def test_strip_html_removes_tags_and_collapses_space(self):
        self.assertEqual(fetch.strip_html("<b>Hello</b>   world"), "Hello world")

    def test_strip_html_unescapes_entities(self):
        self.assertEqual(fetch.strip_html("Tom &amp; Jerry"), "Tom & Jerry")

    def test_strip_html_empty(self):
        self.assertEqual(fetch.strip_html(""), "")

    def test_truncate_words_under_limit_unchanged(self):
        self.assertEqual(fetch.truncate_words("one two three", 5), "one two three")

    def test_truncate_words_over_limit_adds_ellipsis(self):
        self.assertEqual(fetch.truncate_words("a b c d", 2), "a b…")


class LinkSafetyTests(unittest.TestCase):
    def test_http_and_https_allowed(self):
        self.assertEqual(fetch.safe_link("https://x.com/a"), "https://x.com/a")
        self.assertEqual(fetch.safe_link("http://x.com/a"), "http://x.com/a")

    def test_dangerous_schemes_rejected(self):
        self.assertIsNone(fetch.safe_link("javascript:alert(1)"))
        self.assertIsNone(fetch.safe_link("data:text/html,x"))

    def test_relative_or_empty_rejected(self):
        self.assertIsNone(fetch.safe_link("/relative/path"))
        self.assertIsNone(fetch.safe_link(""))


class CategoryTests(unittest.TestCase):
    def test_default_when_missing_or_blank(self):
        self.assertEqual(fetch.source_category({}), fetch.DEFAULT_CATEGORY)
        self.assertEqual(fetch.source_category({"category": "  "}), fetch.DEFAULT_CATEGORY)

    def test_case_insensitive_dedup_keeps_first_spelling(self):
        sources = [{"category": "News"}, {"category": "news"}, {"category": "Tech"}]
        labels = fetch.category_label_map(sources)
        self.assertEqual(labels["news"], "News")
        self.assertEqual(set(labels.values()), {"News", "Tech"})


class ParsePublishedTests(unittest.TestCase):
    def test_full_timestamp(self):
        dt, date_only = fetch.parse_published(entry(published_parsed=struct(2026, 6, 28, 9, 30)))
        self.assertEqual(dt, datetime(2026, 6, 28, 9, 30, tzinfo=timezone.utc))
        self.assertFalse(date_only)

    def test_date_only_flagged(self):
        dt, date_only = fetch.parse_published(entry(published_parsed=struct(2026, 6, 28)))
        self.assertTrue(date_only)

    def test_missing_returns_none(self):
        dt, date_only = fetch.parse_published(entry())
        self.assertIsNone(dt)
        self.assertFalse(date_only)


class WindowFilterTests(unittest.TestCase):
    def titles(self, entries, **kw):
        items, _ = run_feed(entries, **kw)
        return {i["title"] for i in items}

    def test_full_timestamps_inside_and_outside_window(self):
        kept = self.titles([
            entry("in", published_parsed=struct(2026, 6, 28, 5, 0)),     # 2h ago
            entry("edge-in", published_parsed=struct(2026, 6, 27, 8, 0)),  # 23h ago
            entry("out", published_parsed=struct(2026, 6, 27, 6, 0)),     # 25h ago
        ])
        self.assertEqual(kept, {"in", "edge-in"})

    def test_late_last_night_survives_morning_fetch(self):
        kept = self.titles([entry("late", published_parsed=struct(2026, 6, 27, 23, 0))])
        self.assertEqual(kept, {"late"})

    def test_date_only_spans_today_and_yesterday_only(self):
        kept = self.titles([
            entry("today", published_parsed=struct(2026, 6, 28)),
            entry("yesterday", published_parsed=struct(2026, 6, 27)),
            entry("two-days", published_parsed=struct(2026, 6, 26)),
        ])
        self.assertEqual(kept, {"today", "yesterday"})

    def test_undated_kept_and_blank(self):
        items, _ = run_feed([entry("undated")])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["published"], "")

    def test_future_timestamp_clamped_to_now(self):
        items, _ = run_feed([entry("future", published_parsed=struct(2026, 6, 28, 10, 0))])
        self.assertEqual(items[0]["published"], NOW.isoformat())


class ShapeAndOrderTests(unittest.TestCase):
    def test_item_shape(self):
        items, raw = run_feed([entry("A", published_parsed=struct(2026, 6, 28, 5))])
        self.assertEqual(raw, 1)
        self.assertEqual(
            set(items[0]), {"id", "source", "category", "title", "summary", "link", "published"}
        )

    def test_invalid_entries_dropped(self):
        items, raw = run_feed([
            entry(title="bad-link", link="javascript:x", published_parsed=struct(2026, 6, 28, 5)),
            entry(title="", link="https://x/2", published_parsed=struct(2026, 6, 28, 5)),
        ])
        self.assertEqual((len(items), raw), (0, 2))

    def test_max_items_caps_output(self):
        entries = [entry(f"e{n}", link=f"https://x/{n}", published_parsed=struct(2026, 6, 28, 5))
                   for n in range(20)]
        items, _ = run_feed(entries, defaults={**DEFAULTS, "max_items": 5})
        self.assertEqual(len(items), 5)

    def test_recent_order_sorts_newest_first(self):
        entries = [
            entry("old", link="https://x/1", published_parsed=struct(2026, 6, 28, 1)),
            entry("new", link="https://x/2", published_parsed=struct(2026, 6, 28, 6)),
        ]
        items, _ = run_feed(entries, defaults={**DEFAULTS, "order": "recent"})
        self.assertEqual([i["title"] for i in items], ["new", "old"])


if __name__ == "__main__":
    unittest.main()
