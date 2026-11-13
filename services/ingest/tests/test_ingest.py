from datetime import date

import pandas as pd
import pytest
import requests

from src import ingest

DAY = date(2025, 11, 12)

RSS = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>Test</title>
    <item>
      <title>Article d'hier</title>
      <link>https://example.com/hier</link>
      <description>Le resume</description>
      <pubDate>Wed, 12 Nov 2025 08:00:00 GMT</pubDate>
      <dc:creator>"Jeanne Dupont"</dc:creator>
    </item>
    <item>
      <title>Hier, credit photo seulement</title>
      <link>https://example.com/hier-photo</link>
      <pubDate>Wed, 12 Nov 2025 09:00:00 GMT</pubDate>
      <media:credit>Photographe / AFP</media:credit>
    </item>
    <item>
      <title>Article d'aujourd'hui</title>
      <link>https://example.com/aujourdhui</link>
      <pubDate>Thu, 13 Nov 2025 08:00:00 GMT</pubDate>
    </item>
    <item>
      <title>Sans date</title>
      <link>https://example.com/sans-date</link>
    </item>
  </channel>
</rss>
"""


class FakeResponse:
    def __init__(self, content=RSS, status=200):
        self.content = content
        self.status = status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.HTTPError(f"HTTP {self.status}")


# --- clean_journalist -------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "source", "expected"),
    [
        (None, "Le Monde", None),
        ("", "Le Monde", None),
        ('"Robin Lopez"', "L'Union", "Robin Lopez"),
        ("Cyprien Caddeo,Aurélien Soucheyre", "L'Humanité", "Cyprien Caddeo, Aurélien Soucheyre"),
        ("Libération ., AFP .", "Libération", "AFP"),
        ("leJDD", "Le JDD", None),  # le nom du média n'est pas un journaliste
        ("A, B, A", "X", "A, B"),  # doublons retirés
    ],
)
def test_clean_journalist(raw, source, expected):
    assert ingest.clean_journalist(raw, source) == expected


# --- fetch ------------------------------------------------------------------


def test_fetch_keeps_only_target_day(monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse())

    rows = ingest.fetch({"name": "Test", "url": "https://example.com/rss"}, DAY)

    assert [r["link"] for r in rows] == [
        "https://example.com/hier",
        "https://example.com/hier-photo",
    ]
    first = rows[0]
    assert set(first) == {"source", "title", "excerpt", "link", "date", "journalist"}
    assert first["source"] == "Test"
    assert first["title"] == "Article d'hier"
    assert first["excerpt"] == "Le resume"
    assert first["date"].startswith("2025-11-12T08:00:00")
    assert first["journalist"] == "Jeanne Dupont"


def test_fetch_does_not_use_media_credit_as_journalist(monkeypatch):
    monkeypatch.setattr(ingest.requests, "get", lambda *a, **k: FakeResponse())

    rows = ingest.fetch({"name": "Test", "url": "https://example.com/rss"}, DAY)

    assert rows[1]["journalist"] is None


def test_fetch_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        ingest.requests, "get", lambda *a, **k: FakeResponse(b"", status=429)
    )
    with pytest.raises(requests.HTTPError):
        ingest.fetch({"name": "X", "url": "https://x"}, DAY)


# --- main -------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    config = tmp_path / "sources.yml"
    config.write_text(
        "feeds:\n"
        "  - {id: ok, name: Ok, url: 'https://ok/rss'}\n"
        "  - {id: other, name: Other, url: 'https://other/rss'}\n"
        "  - {id: down, name: Down, url: 'https://down/rss'}\n"
        "  - {id: missing, name: Missing, url: null}\n",
        encoding="utf-8",
    )
    out = tmp_path / "bronze"
    monkeypatch.setattr(ingest, "FEEDS", str(config))
    monkeypatch.setattr(ingest, "OUT", str(out))
    monkeypatch.delenv("SOURCES", raising=False)

    def fake_get(url, **kwargs):
        return FakeResponse(status=500) if "down" in url else FakeResponse()

    monkeypatch.setattr(ingest.requests, "get", fake_get)
    return out


def test_main_writes_one_parquet_per_source(workspace, capsys):
    ingest.main(DAY)

    ok = workspace / "source=ok" / "date=2025-11-12" / "articles.parquet"
    df = pd.read_parquet(ok)
    assert len(df) == 2
    assert df["journalist"].tolist() == ["Jeanne Dupont", None]
    assert (workspace / "source=other" / "date=2025-11-12").exists()


def test_main_skips_failing_and_urlless_sources(workspace, capsys):
    ingest.main(DAY)

    assert not (workspace / "source=down").exists()
    assert not (workspace / "source=missing").exists()
    captured = capsys.readouterr()
    assert "[WARN] down" in captured.err
    assert "4 articles" in captured.out


def test_main_sources_env_filters(workspace, monkeypatch):
    monkeypatch.setenv("SOURCES", "other")

    ingest.main(DAY)

    assert [p.name for p in workspace.iterdir()] == ["source=other"]


def test_main_defaults_to_yesterday(workspace, monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return cls(2025, 11, 13)

    monkeypatch.setattr(ingest, "date", FixedDate)

    ingest.main()

    assert (workspace / "source=ok" / "date=2025-11-12").exists()
