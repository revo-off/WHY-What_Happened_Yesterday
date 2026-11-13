"""Download RSS feeds and write the previous day's articles to Parquet."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import feedparser
import pandas as pd
import requests
import yaml

DEFAULT_FEEDS_FILE = Path("/config/sources.yml")
DEFAULT_OUT_DIR = Path("/data/bronze")
USER_AGENT = "WHY-What-Happened-Yesterday/1.0"
REQUEST_TIMEOUT = 30


def load_feeds(path: Path) -> dict[str, dict[str, Any]]:
    """Load configured RSS feeds, indexed by their stable source id."""
    with path.open(encoding="utf-8") as file:
        config = yaml.safe_load(file) or {}

    feeds = config.get("feeds", [])
    if not isinstance(feeds, list):
        raise ValueError(f"Expected a list of feeds in {path}")

    result: dict[str, dict[str, Any]] = {}
    for feed in feeds:
        if not isinstance(feed, dict) or not feed.get("id") or not feed.get("url"):
            continue
        result[str(feed["id"])] = feed
    return result


def selected_feeds(
    feeds: dict[str, dict[str, Any]], sources: str | None
) -> list[dict[str, Any]]:
    """Select feeds from SOURCES; an empty value means every configured feed."""
    if not sources:
        return list(feeds.values())

    selected: list[dict[str, Any]] = []
    for source_id in (value.strip() for value in sources.split(",")):
        if not source_id:
            continue
        if source_id not in feeds:
            raise ValueError(f"Unknown RSS source: {source_id}")
        selected.append(feeds[source_id])
    return selected


def parse_journalist(entry: Any) -> str:
    """Return the credit from <author>, else media:credit."""
    author = str(entry.get("author", "")).strip()
    if author:
        return author

    credit = entry.get("media_credit", "")
    if isinstance(credit, list):
        credit = credit[0] if credit else ""
    if isinstance(credit, dict):
        credit = credit.get("content", "")
    return str(credit).strip()


def parse_image_caption(entry: Any) -> str:
    """Return media:description, which feedparser exposes as entry.content."""
    content = entry.get("content", "")
    if isinstance(content, list):
        content = content[0] if content else ""
    if isinstance(content, dict):
        content = content.get("value", "")
    return str(content).strip()


def parse_image_url(entry: Any) -> str:
    """Return the media:content image url."""
    media = entry.get("media_content", "")
    if isinstance(media, list):
        media = media[0] if media else ""
    if isinstance(media, dict):
        media = media.get("url", "")
    return str(media).strip()


def to_utc(parsed_time: Any) -> datetime | None:
    """Convert a feedparser time tuple (already UTC) to an aware datetime."""
    if parsed_time is None:
        return None
    return datetime(*parsed_time[:6], tzinfo=UTC)


def parse_published_at(entry: Any) -> datetime | None:
    """Return the reference date: <updated> when present, else <pubDate>.

    feedparser aliases updated_parsed to published_parsed when the feed has no
    <updated> tag, so preferring it degrades to <pubDate> on its own.
    """
    return to_utc(entry.get("updated_parsed") or entry.get("published_parsed"))


def fetch_articles(feed: dict[str, Any], target_date: date) -> list[dict[str, Any]]:
    """Fetch one RSS feed and keep entries published on target_date."""
    response = requests.get(
        str(feed["url"]),
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    parsed_feed = feedparser.parse(response.content)
    if parsed_feed.bozo and not parsed_feed.entries:
        raise ValueError(f"Invalid RSS response from {feed['url']}")

    articles: list[dict[str, Any]] = []
    for entry in parsed_feed.entries:
        reference_at = parse_published_at(entry)
        if reference_at is None or reference_at.date() != target_date:
            continue

        pub_date = to_utc(entry.get("published_parsed"))
        updated_at = to_utc(entry.get("updated_parsed"))
        image_caption = parse_image_caption(entry)
        excerpt = str(entry.get("summary", "")).strip()
        articles.append(
            {
                "source": str(feed["id"]),
                "title": str(entry.get("title", "")).strip(),
                "excerpt": excerpt or image_caption,
                "image_caption": image_caption,
                "image_url": parse_image_url(entry),
                "link": str(entry.get("link", "")).strip(),
                "date": reference_at.date().isoformat(),
                "published_at": reference_at.isoformat(),
                "pub_date": pub_date.isoformat() if pub_date else "",
                "updated_at": updated_at.isoformat() if updated_at else "",
                "journalist": parse_journalist(entry),
            }
        )
    return articles


def write_articles(
    articles: list[dict[str, Any]],
    output_dir: Path,
    source_id: str,
    target_date: date,
) -> Path:
    """Write articles to the documented bronze partition."""
    destination = (
        output_dir
        / f"source={source_id}"
        / f"date={target_date.isoformat()}"
        / "articles.parquet"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = [
        "source",
        "title",
        "excerpt",
        "image_caption",
        "image_url",
        "link",
        "date",
        "published_at",
        "pub_date",
        "updated_at",
        "journalist",
    ]
    pd.DataFrame(articles, columns=columns).to_parquet(destination, index=False)
    return destination


def main() -> None:
    """Run ingestion for the configured sources."""
    feeds_file = Path(os.getenv("FEEDS_FILE", str(DEFAULT_FEEDS_FILE)))
    output_dir = Path(os.getenv("OUT_DIR", str(DEFAULT_OUT_DIR)))
    sources = os.getenv("SOURCES", "lemonde")
    target_date = date.today() - timedelta(days=1)

    feeds = load_feeds(feeds_file)
    for feed in selected_feeds(feeds, sources):
        articles = fetch_articles(feed, target_date)
        destination = write_articles(articles, output_dir, str(feed["id"]), target_date)
        print(f"{feed['id']}: {len(articles)} articles -> {destination}")


if __name__ == "__main__":
    main()
