# Headline Briefing

A personal, low-engagement news headline page, *[Headline Briefing](https://jackhallybone.github.io/headline-briefing/)*.

Designed to give "situational awareness" rather than in-depth knowledge, with a low-engagement design, by periodically fetching headlines and summaries from RSS feeds. Minimal publisher data is saved and it is not (intentionally) retained longer than needed.

:sparkles: "Co-authored" with Claude.

## Config

Everything is set in `config.yaml`. The `defaults` block defines how source data should be filtered and ordered. The `sources` block lists each source name (`source`), RSS feed `url` and `category` for grouping. Each source can individually overwrite the default parameters.

```yaml
defaults:
  window_hours: 24        # how far back to look
  max_items: 10           # cap per source, so one busy feed can't dominate
  order: feed             # recent = newest first; feed = keep the feed's own order
  summary_word_limit: 50  # truncate long summaries

sources:
  - source: BBC News
    category: News
    url: https://feeds.bbci.co.uk/news/rss.xml
    # e.g., `max_items: 20` to override the default limit
```

## Data

RSS data is fetched periodically based on the UTC `cron` schedule in
`.github/workflows/fetch.yaml` and filtered based on the settings in `config.yaml`. The intention is to provide a limited "daily headline briefing" rather than a live updating, infinite scroll news site.

Only the minimum filtered data required for the page is saved, and is not retained longer than is necessary (for example by not storing it in the git history).
