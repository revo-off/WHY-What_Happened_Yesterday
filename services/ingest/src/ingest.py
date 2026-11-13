"""Extrait les articles de la veille depuis les flux RSS -> Parquet brut (couche bronze).

Un fichier par source et par jour : bronze/source=<id>/date=<YYYY-MM-DD>/articles.parquet
Colonnes : source, title, excerpt, link, date, journalist (null si absent du flux, pas de scraping).
"""
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from time import mktime

import feedparser
import pandas as pd
import requests
import yaml

FEEDS = os.getenv("FEEDS_FILE", "/config/sources.yml")
OUT = os.getenv("OUT_DIR", "/data/bronze")
UA = "Mozilla/5.0 (compatible; WHY-bot/0.1)"


def clean_journalist(raw, source_name):
    """Normalise l'auteur issu du flux (author / dc:creator). Null si absent ou si c'est le nom du média."""
    if not raw:
        return None
    norm = lambda s: re.sub(r"[\W_]+", "", s).lower()
    names = []
    for n in re.split(r"[,;]", raw):
        n = re.sub(r"\s+", " ", n.strip(" \t\"'«».")).strip()
        if n and norm(n) != norm(source_name) and n not in names:
            names.append(n)
    return ", ".join(names) or None


def fetch(feed, day):
    r = requests.get(feed["url"], headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    rows = []
    for e in feedparser.parse(r.content).entries:
        ts = e.get("published_parsed") or e.get("updated_parsed")
        if not ts:
            continue
        dt = datetime.fromtimestamp(mktime(ts), tz=timezone.utc)
        if dt.date() != day:
            continue
        rows.append({
            "source": feed["name"],
            "title": e.get("title"),
            "excerpt": e.get("summary"),
            "link": e.get("link"),
            "date": dt.isoformat(),
            "journalist": clean_journalist(e.get("author"), feed["name"]),
        })
    return rows


def main(day=None):
    day = day or date.today() - timedelta(days=1)
    with open(FEEDS, encoding="utf-8") as fh:
        feeds = [f for f in yaml.safe_load(fh)["feeds"] if f.get("url")]
    only = [s for s in os.getenv("SOURCES", "").split(",") if s]  # ex: SOURCES=lemonde
    if only:
        feeds = [f for f in feeds if f["id"] in only]
    total = 0
    for f in feeds:
        try:
            rows = fetch(f, day)
        except Exception as ex:
            print(f"[WARN] {f['id']}: {ex}", file=sys.stderr)
            continue
        if not rows:
            continue
        d = f"{OUT}/source={f['id']}/date={day}"
        os.makedirs(d, exist_ok=True)
        pd.DataFrame(rows).to_parquet(f"{d}/articles.parquet", index=False)
        total += len(rows)
    print(f"{total} articles ({day}) -> {OUT}")


if __name__ == "__main__":
    main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None)
