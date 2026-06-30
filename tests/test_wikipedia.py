"""Unit tests for fetch_wiki_current_events.py. Uses a synthetic wikitext
fixture in the real Portal:Current events format (no real article content), so
there is no network and no third-party text committed.
"""
import sys
import types
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fetch_wiki_current_events as wce

# Mirrors the real structure: a {{Current events}} wrapper, '''section''' headings,
# nested *-bullets where wholly-[[link]] bullets are topic headers and prose
# bullets (at any depth) are the actual items, with [url (Source)] citations.
FIXTURE = """{{Current events|year=2026|month=01|day=15|content=

<!-- All news items below this line -->
'''Armed conflicts and attacks'''
*[[Example conflict]]
**[[Example sub-conflict]]
***The [[Example Army|army]] of [[Examplestan]] reports an incident near [[Example City]]. [https://example.com/a (Example News)]
*[[Another topic]], [[Second topic]]
**Forces clash in [[Border Region]], with several reported casualties. [https://example.com/b (Example Wire)] [https://example.com/b2 (Second Source)]
'''Politics and elections'''
*Voters in [[Sampleland]] head to the polls in a [[2026 Sampleland election|general election]]. [https://example.com/c (Sample Times)]
<!-- All news items above this line -->}}"""

DAY = date(2026, 1, 15)


class HelperTests(unittest.TestCase):
    def test_day_page_title(self):
        self.assertEqual(wce.day_page_title(date(2026, 6, 28)),
                         "Portal:Current events/2026 June 28")

    def test_is_topic_header_true_for_wholly_links(self):
        self.assertTrue(wce._is_topic_header("[[Example conflict]]"))
        self.assertTrue(wce._is_topic_header("[[Another topic]], [[Second topic]]"))

    def test_is_topic_header_false_for_prose(self):
        self.assertFalse(wce._is_topic_header("The [[army]] reports an incident."))

    def test_to_text_resolves_links_and_drops_citations(self):
        self.assertEqual(
            wce._to_text("The [[Example Army|army]] of [[Examplestan]] acts. [https://x (Src)]"),
            "The army of Examplestan acts.",
        )

    def test_to_text_handles_link_trail_and_emphasis(self):
        self.assertEqual(wce._to_text("[[Iran]]ian ''forces'' move"), "Iranian forces move")


class ParseDayTests(unittest.TestCase):
    def setUp(self):
        self.items = wce.parse_day(FIXTURE, DAY)

    def test_only_prose_bullets_kept(self):
        texts = [i["text"] for i in self.items]
        self.assertEqual(texts, [
            "The army of Examplestan reports an incident near Example City.",
            "Forces clash in Border Region, with several reported casualties.",
            "Voters in Sampleland head to the polls in a general election.",
        ])

    def test_topic_headers_excluded(self):
        joined = " ".join(i["text"] for i in self.items)
        for header in ("Example conflict", "Example sub-conflict", "Another topic", "Second topic"):
            self.assertNotIn(header, joined)

    def test_sections_become_source_tags(self):
        sections = [i["section"] for i in self.items]
        self.assertEqual(sections, [
            "Armed conflicts and attacks",
            "Armed conflicts and attacks",
            "Politics and elections",
        ])

    def test_no_markup_leaks(self):
        for item in self.items:
            for token in ("[[", "]]", "[http", "'''", "''", "<!--"):
                self.assertNotIn(token, item["text"])

    def test_each_item_tagged_with_date(self):
        self.assertTrue(all(i["date"] == "2026-01-15" for i in self.items))


class FetchFeedTests(unittest.TestCase):
    def setUp(self):
        # Stub the network: one day's events from the fixture.
        self._orig = wce.recent_events
        wce.recent_events = lambda days=2, session=None, today=None: wce.parse_day(FIXTURE, DAY)

    def tearDown(self):
        wce.recent_events = self._orig

    def fetch(self, max_items=10):
        now = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)
        defaults = {"window_hours": 24, "max_items": max_items, "order": "feed", "summary_word_limit": 50}
        source = {"source": "Wikipedia Current Events", "url": wce_url(), "category": "Wikipedia"}
        return wce.fetch_feed(source, defaults, now, "Wikipedia")

    def test_item_shape_and_constants(self):
        items, raw = self.fetch()
        self.assertEqual(raw, 3)
        for item in items:
            self.assertEqual(
                set(item), {"id", "source", "category", "title", "summary", "link", "published"}
            )
            self.assertEqual(item["summary"], "")
            self.assertEqual(item["link"], wce.PORTAL_URL)
            self.assertEqual(item["category"], "Wikipedia")

    def test_section_becomes_source(self):
        items, _ = self.fetch()
        self.assertEqual(items[0]["source"], "Armed conflicts and attacks")
        self.assertEqual(items[-1]["source"], "Politics and elections")

    def test_ids_unique_despite_identical_links(self):
        items, _ = self.fetch()
        ids = [i["id"] for i in items]
        self.assertEqual(len(ids), len(set(ids)))

    def test_max_items_caps_per_section(self):
        items, _ = self.fetch(max_items=1)
        per_section = {}
        for i in items:
            per_section[i["source"]] = per_section.get(i["source"], 0) + 1
        self.assertTrue(all(c <= 1 for c in per_section.values()))


def wce_url():
    # The sentinel fetch.py uses to route to this module.
    return "wikipedia:current-events"


if __name__ == "__main__":
    unittest.main()
