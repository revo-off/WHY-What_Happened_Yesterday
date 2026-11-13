import time
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pytest

from src import ingest

REPO_ROOT = Path(__file__).resolve().parents[3]

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">
  <channel>
    <title>Test</title>
    <item>
      <title>Article d'hier</title>
      <link>https://example.com/hier</link>
      <description>Le resume</description>
      <pubDate>Wed, 12 Nov 2025 08:00:00 GMT</pubDate>
      <author>Jeanne Dupont</author>
      <media:content url="https://example.com/img.jpg" medium="image">
        <media:description>Legende</media:description>
      </media:content>
    </item>
    <item>
      <title>Article d'aujourd'hui</title>
      <link>https://example.com/aujourdhui</link>
      <pubDate>Thu, 13 Nov 2025 08:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>
"""


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200):
        self.content = content
        self.status = status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


# --- configuration ---------------------------------------------------------


def test_load_feeds_skips_invalid_entries(tmp_path):
    config = tmp_path / "sources.yml"
    config.write_text(
        "feeds:\n"
        "  - {id: a, url: 'https://a'}\n"
        "  - {id: b}\n"  # pas d'url
        "  - {url: 'https://c'}\n",  # pas d'id
        encoding="utf-8",
    )
    assert list(ingest.load_feeds(config)) == ["a"]


def test_load_feeds_rejects_non_list(tmp_path):
    config = tmp_path / "sources.yml"
    config.write_text("feeds: {id: a}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        ingest.load_feeds(config)


def test_repository_sources_file_is_valid():
    feeds = ingest.load_feeds(REPO_ROOT / "config" / "sources.yml")
    assert "lemonde" in feeds
    assert all(str(f["url"]).startswith("https://") for f in feeds.values())


def test_selected_feeds():
    feeds = {"a": {"id": "a"}, "b": {"id": "b"}}
    assert ingest.selected_feeds(feeds, "") == [{"id": "a"}, {"id": "b"}]
    assert ingest.selected_feeds(feeds, " b , ") == [{"id": "b"}]
    with pytest.raises(ValueError, match="Unknown RSS source"):
        ingest.selected_feeds(feeds, "zzz")


# --- parsing ---------------------------------------------------------------


def test_parse_journalist_prefers_author_then_media_credit():
    assert ingest.parse_journalist({"author": " A "}) == "A"
    assert ingest.parse_journalist({"media_credit": [{"content": "B"}]}) == "B"
    assert ingest.parse_journalist({}) == ""


def test_parse_image_fields():
    entry = {
        "content": [{"value": " legende "}],
        "media_content": [{"url": "https://x/img.jpg"}],
    }
    assert ingest.parse_image_caption(entry) == "legende"
    assert ingest.parse_image_url(entry) == "https://x/img.jpg"
    assert ingest.parse_image_caption({}) == ""
    assert ingest.parse_image_url({"media_content": []}) == ""


def test_parse_published_at_prefers_updated():
    updated = time.strptime("2025-11-12 10:00:00", "%Y-%m-%d %H:%M:%S")
    published = time.strptime("2025-11-11 10:00:00", "%Y-%m-%d %H:%M:%S")
    entry = {"updated_parsed": updated, "published_parsed": published}
    assert ingest.parse_published_at(entry) == datetime(2025, 11, 12, 10, tzinfo=UTC)
    assert ingest.parse_published_at({}) is None


# --- fetch + écriture --------------------------------------------------------


def test_fetch_articles_keeps_only_target_date(monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse(RSS))
    feed = {"id": "test", "url": "https://example.com/rss"}

    articles = ingest.fetch_articles(feed, date(2025, 11, 12))

    assert len(articles) == 1
    article = articles[0]
    assert article["source"] == "test"
    assert article["title"] == "Article d'hier"
    assert article["date"] == "2025-11-12"
    assert article["journalist"] == "Jeanne Dupont"
    assert article["image_url"] == "https://example.com/img.jpg"


def test_fetch_articles_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: FakeResponse(b"", status=429)
    )
    with pytest.raises(RuntimeError):
        ingest.fetch_articles({"id": "x", "url": "https://x"}, date(2025, 11, 12))


def test_write_articles_creates_bronze_partition(tmp_path):
    articles = [{"source": "lemonde", "title": "T", "date": "2025-11-12"}]

    path = ingest.write_articles(articles, tmp_path, "lemonde", date(2025, 11, 12))

    assert path == (
        tmp_path / "source=lemonde" / "date=2025-11-12" / "articles.parquet"
    )
    df = pd.read_parquet(path)
    assert len(df) == 1
    assert "journalist" in df.columns  # schéma complet même si la colonne manque


def test_write_articles_empty_day_still_writes_schema(tmp_path):
    path = ingest.write_articles([], tmp_path, "lemonde", date(2025, 11, 12))
    assert pd.read_parquet(path).empty
