#!/usr/bin/env python3
"""Fetch RSS feeds from config.yaml, write a flat JSON cache of headlines.

Usage: fetch_feeds.py [--out PATH]
"""

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path

import feedparser
import requests
import yaml

CONFIG_PATH = Path(__file__).parent / "config.yaml"

DATE_ONLY_GRACE_HOURS = (
    24  # extra slack for feeds that give a date with no time (e.g. Nature)
)
SAFETY_CAP_PER_FEED = 100  # hard backstop for ingesting the feed before filtering
FETCH_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 5_000_000
ALLOWED_LINK_SCHEMES = {"http", "https"}  # blocks javascript:, data:, etc.
USER_AGENT = "Mozilla/5.0 (compatible; RSS reader)"  # some publishers restrict obvious bot UAs

TAG_RE = re.compile(r"<[^>]+>")


def strip_html(text: str) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", unescape(TAG_RE.sub("", text))).strip()


def truncate_words(text: str, limit: int) -> str:
    words = text.split(" ")
    if len(words) <= limit:
        return text
    return " ".join(words[:limit]) + "…"


def safe_link(url: str) -> str | None:
    url = (url or "").strip()
    scheme = url.split(":", 1)[0].lower() if ":" in url else ""
    return url if scheme in ALLOWED_LINK_SCHEMES else None


def parse_published(entry) -> tuple[str, bool]:
    """Returns (ISO timestamp, is_date_only). is_date_only flags an exact
    00:00:00 time, which usually means the feed gave a date with no time
    (see DATE_ONLY_GRACE_HOURS)."""
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            iso = datetime(*value[:6], tzinfo=timezone.utc).isoformat()
            return iso, value[3:6] == (0, 0, 0)
    return "", False


def fetch_bytes(url: str) -> bytes:
    """Fetch with a timeout and a hard size cap (rejects mid-stream, not after buffering)."""
    with requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=FETCH_TIMEOUT_SECONDS,
        stream=True,
    ) as response:
        response.raise_for_status()
        chunks, total = [], 0
        for chunk in response.iter_content(65536):
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} byte cap")
            chunks.append(chunk)
        return b"".join(chunks)


def resolve_settings(source: dict, defaults: dict) -> dict:
    """A source inherits each setting from `defaults` unless it has its
    own key. Keeps config.yaml sources free of repeated boilerplate."""
    return {
        "window_hours": source.get("window_hours", defaults["window_hours"]),
        "max_items": source.get("max_items", defaults["max_items"]),
        "order": source.get("order", defaults["order"]),
        "summary_word_limit": source.get("summary_word_limit", defaults["summary_word_limit"]),
    }


def category_label_map(sources: list[dict]) -> dict[str, str]:
    """Map each lower-cased category to one sanitised display label, so that
    "news" and "News" collapse to a single tab. First spelling in config wins."""
    labels: dict[str, str] = {}
    for source in sources:
        label = strip_html(source["category"])
        labels.setdefault(label.lower(), label)
    return labels


def fetch_feed(
    source: dict, defaults: dict, now: datetime, category: str
) -> tuple[list[dict], int]:
    settings = resolve_settings(source, defaults)
    cutoff = now - timedelta(hours=settings["window_hours"])
    date_only_cutoff = cutoff - timedelta(hours=DATE_ONLY_GRACE_HOURS)
    source_name = strip_html(source["source"])

    raw = fetch_bytes(source["url"])
    parsed = feedparser.parse(raw)  # sanitize_html=True by default
    raw_entry_count = len(parsed.entries)

    items = []
    for entry in parsed.entries[:SAFETY_CAP_PER_FEED]:
        title = strip_html(getattr(entry, "title", ""))
        link = safe_link(getattr(entry, "link", ""))
        summary = truncate_words(
            strip_html(
                getattr(entry, "summary", "") or getattr(entry, "description", "")
            ),
            settings["summary_word_limit"],
        )
        published, is_date_only = parse_published(entry)
        if not title or not link:
            continue
        if published:
            effective_cutoff = date_only_cutoff if is_date_only else cutoff
            if datetime.fromisoformat(published) < effective_cutoff:
                continue
        items.append(
            {
                # Stable per-article id (hash of the link), so the page can
                # remember which headlines you've opened.
                "id": hashlib.sha256(link.encode("utf-8")).hexdigest()[:12],
                "source": source_name,
                "category": category,
                "title": title,
                "summary": summary,
                "link": link,
                "published": published,
            }
        )

    # recent = newest first; feed = keep the feed's own order
    if settings["order"] == "recent":
        items.sort(key=lambda i: i["published"] or "", reverse=True)

    return items[: settings["max_items"]], raw_entry_count


def build_cache(config: dict) -> dict:
    defaults = config["defaults"]
    sources = config["sources"]
    now = datetime.now(timezone.utc)

    labels = category_label_map(sources)
    items, errors, fetched = [], [], 0

    for source in sources:
        category = labels[strip_html(source["category"]).lower()]
        try:
            feed_items, raw_entry_count = fetch_feed(source, defaults, now, category)
            fetched += 1
            if not raw_entry_count:
                errors.append(f"{source['source']}: no items found")
            items.extend(feed_items)
        except Exception as exc:  # one bad feed shouldn't kill the run
            errors.append(f"{source['source']}: {exc}")

    if not fetched:
        raise RuntimeError("every source failed to fetch; keeping the previous deploy")

    return {
        "generated_at": now.isoformat(),
        "categories": sorted(
            labels.values(), key=str.lower
        ),  # tabs: alphabetical, first is the default
        "sources": [
            strip_html(s["source"]) for s in sources
        ],  # config.yaml order, for stable display grouping
        "errors": errors,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="data/data.json")
    args = parser.parse_args()

    print("Fetching...")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    try:
        cache = build_cache(config)
    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(
        f"Wrote {len(cache['items'])} items from {len(config['sources'])} sources to {out_path}."
    )
    if cache["errors"]:
        print("Errors:", *cache["errors"], sep="\n  - ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
