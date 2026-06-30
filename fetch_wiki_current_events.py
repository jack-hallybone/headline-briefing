#!/usr/bin/env python3
"""Parse Wikipedia's "Portal:Current events" day pages into headline items.

Standalone and self-contained: it only needs ``requests`` (already a project
dependency) plus the standard library, and it imports nothing from the rest of
the codebase. ``fetch_feed`` at the bottom is a drop-in for fetch.py's RSS
fetcher, wired in via the ``wikipedia:current-events`` config sentinel.

Why wikitext, not the rendered HTML
-----------------------------------
We ask the MediaWiki ``action=parse`` API for each day's *wikitext* rather than
its HTML. The portal's HTML wrapper (``<div class="current-events-content">``
and friends) gets restyled every few years, but the underlying wiki markup is
hand-typed by editors and has been stable for ~20 years:

* sections are ``'''Bold heading'''`` lines;
* events are ``*`` bullets, nested with extra stars;
* a bullet that is *wholly* ``[[wikilinks]]`` is a topic header pointing at
  background articles, while a bullet containing prose is an actual news item.

So we split on the headings, keep only the prose bullets (skipping the
wholly-link topic headers, at whatever depth they sit), and strip the markup
down to plain text. Each day is fetched independently, so a missing or
unreachable day is skipped, not fatal.

Run directly to print the current and previous day:

    python fetch_wiki_current_events.py
"""
from __future__ import annotations

import hashlib
import re
import sys
from datetime import date, datetime, timedelta, timezone
from math import ceil

import requests

API_URL = "https://en.wikipedia.org/w/api.php"
# Every headline links here -- the portal page the events were read from --
# because the day pages have no per-item permalinks worth copying.
PORTAL_URL = "https://en.wikipedia.org/wiki/Portal:Current_events"
# Wikipedia asks API clients to send a descriptive User-Agent.
USER_AGENT = "headline-briefing/1.0 (+https://github.com/jack-hallybone/headline-briefing)"
REQUEST_TIMEOUT = 15

# Locale-independent month names (datetime's %B follows the C locale).
_MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

# The template wraps the body in these boilerplate comments; they are the most
# stable anchors for the events region.
_BELOW_MARKER = "<!-- All news items below this line -->"
_ABOVE_MARKER = "<!-- All news items above this line -->"

_WHITESPACE = re.compile(r"\s+")
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^'''(.+?)'''$")
_BULLET = re.compile(r"^\*+\s*(.*)$")
_WIKILINK = re.compile(r"\[\[(?:[^\]|]*\|)?([^\]|]+)\]\]")  # [[a|b]] -> b, [[a]] -> a
_WIKILINK_WHOLE = re.compile(r"\[\[[^\]]*\]\]")             # the whole link token
_EXTLINK = re.compile(r"\[https?://[^\]]*\]")              # [url (Source)] citation
_EMPHASIS = re.compile(r"'{2,}")                           # ''italic'' / '''bold'''
# After stripping links/emphasis, a topic header has only this punctuation left.
_HEADER_RESIDUE = re.compile(r"[^\s,;/&]")


def day_page_title(day: date) -> str:
    """e.g. date(2026, 6, 28) -> 'Portal:Current events/2026 June 28'."""
    return f"Portal:Current events/{day.year} {_MONTHS[day.month - 1]} {day.day}"


def _content_region(wikitext: str) -> str:
    """The events body inside the ``{{Current events|...|content=...}}`` wrapper.

    Prefers the template's own 'news items below/above this line' comments, and
    falls back to the ``content=`` parameter and trailing ``}}`` if those move.
    """
    text = wikitext
    if _BELOW_MARKER in text and _ABOVE_MARKER in text:
        text = text.split(_BELOW_MARKER, 1)[1].split(_ABOVE_MARKER, 1)[0]
    else:
        if "content=" in text:
            text = text.split("content=", 1)[1]
        text = text.rsplit("}}", 1)[0]
    return _COMMENT.sub("", text)


def _to_text(markup: str) -> str:
    """Render a line's wiki markup to plain text: resolve ``[[a|b]]`` -> ``b``,
    drop ``[url (Source)]`` citations and ``''``/``'''`` emphasis, collapse
    whitespace."""
    text = _EXTLINK.sub("", markup)     # drop external source citations
    text = _WIKILINK.sub(r"\1", text)   # [[Iran]]ian -> Iranian; [[a|b]] -> b
    text = _EMPHASIS.sub("", text)      # ''italic'' / '''bold''' markers
    return _WHITESPACE.sub(" ", text).strip()


def _is_topic_header(markup: str) -> bool:
    """True when a bullet is *wholly* links (a navigational topic header such as
    ``[[2026 Iran war]]`` or ``[[A]], [[B]]``) -- i.e. it has no prose of its
    own once the links, citations and emphasis are removed."""
    residue = _WIKILINK_WHOLE.sub("", markup)
    residue = _EXTLINK.sub("", residue)
    residue = _EMPHASIS.sub("", residue)
    return _HEADER_RESIDUE.search(residue) is None


def parse_day(wikitext: str, day: date) -> list[dict]:
    """Extract a day's prose news items, each tagged with its section heading.

    Returns ``[{date, section, text}, ...]`` in page order. Wholly-link topic
    headers are skipped; only prose bullets become items.
    """
    iso = day.isoformat()
    section = ""
    items: list[dict] = []
    for raw_line in _content_region(wikitext).splitlines():
        line = raw_line.strip()
        if not line:
            continue
        heading = _HEADING.match(line)
        if heading:
            section = _to_text(heading.group(1))
            continue
        bullet = _BULLET.match(line)
        if not bullet:
            continue
        markup = bullet.group(1).strip()
        if not markup or _is_topic_header(markup):
            continue
        text = _to_text(markup)
        if text:
            items.append({"date": iso, "section": section, "text": text})
    return items


def fetch_day_wikitext(day: date, session: requests.Session) -> str | None:
    """Return a day's subpage wikitext, or None if the page is missing."""
    params = {
        "action": "parse",
        "page": day_page_title(day),
        "prop": "wikitext",
        "formatversion": "2",
        "format": "json",
        "redirects": "1",
    }
    resp = session.get(
        API_URL, params=params, timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:        # e.g. the day's page doesn't exist yet
        return None
    return data.get("parse", {}).get("wikitext")


def recent_events(
    days: int = 2,
    session: requests.Session | None = None,
    today: date | None = None,
) -> list[dict]:
    """Prose news items for ``today`` and the preceding ``days - 1`` days (UTC).

    Each item is ``{date, section, text}``, in page order, with the newest day
    first.
    """
    session = session or requests.Session()
    today = today or datetime.now(timezone.utc).date()
    events: list[dict] = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        try:
            wikitext = fetch_day_wikitext(day, session)
        except requests.RequestException as exc:
            print(f"WARN: could not fetch {day}: {exc}", file=sys.stderr)
            continue
        if wikitext:
            events.extend(parse_day(wikitext, day))
    return events


def fetch_feed(
    source: dict, defaults: dict, now: datetime, category: str
) -> tuple[list[dict], int]:
    """Drop-in for fetch.py's ``fetch_feed`` behind the config sentinel.

    Emits items in the RSS item shape (id / source / category / title / summary
    / link / published) so ``build_cache`` groups and displays them identically.
    Each section heading becomes a ``source``; each prose bullet a ``title`` with
    no summary; every link points at the portal page.

    ``window_hours`` is read as a number of days, rounded up (24 -> today,
    48 -> today + yesterday). ``max_items`` caps headlines *per section*, so one
    busy section can't crowd the others out.
    """
    window_hours = source.get("window_hours", defaults["window_hours"])
    max_items = source.get("max_items", defaults["max_items"])
    days = max(1, ceil(window_hours / 24))

    events = recent_events(days=days, today=now.date())

    items: list[dict] = []
    per_section: dict[str, int] = {}
    for event in events:
        section = event["section"] or "Current events"
        if per_section.get(section, 0) >= max_items:
            continue
        per_section[section] = per_section.get(section, 0) + 1
        # Every link is the same portal URL, so the id hashes the text (with its
        # day and section) instead -- otherwise read-tracking would treat every
        # headline as the same story.
        key = f"{event['date']}\n{section}\n{event['text']}"
        items.append(
            {
                "id": hashlib.sha256(key.encode("utf-8")).hexdigest()[:12],
                "source": section,
                "category": category,
                "title": event["text"],
                "summary": "",
                "link": PORTAL_URL,
                "published": f"{event['date']}T00:00:00+00:00",
            }
        )
    return items, len(events)


def main() -> int:
    events = recent_events()
    if not events:
        print("No events found (page missing or network blocked).", file=sys.stderr)
        return 1
    current_day = current_section = None
    for event in events:
        if event["date"] != current_day:
            current_day = event["date"]
            current_section = None
            print(f"\n=== {current_day} ===")
        if event["section"] != current_section:
            current_section = event["section"]
            print(f"\n  [{current_section}]")
        print(f"  - {event['text']}")
    print(f"\n{len(events)} events.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
