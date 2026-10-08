"""JSON 'database' committed to the repo (data/articles.json)."""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
ARTICLES_FILE = DATA_DIR / "articles.json"


def load() -> list[dict]:
    if ARTICLES_FILE.exists():
        return json.loads(ARTICLES_FILE.read_text(encoding="utf-8"))
    return []


def save(articles: list[dict]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    articles.sort(key=lambda a: a["published"], reverse=True)
    articles = articles[: config.MAX_STORED_ARTICLES]
    tmp = ARTICLES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(articles, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(ARTICLES_FILE)


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower())


class Deduper:
    """Rejects items already published, or the same story from another outlet."""

    def __init__(self, articles: list[dict]):
        self.ids = {a["id"] for a in articles}
        self.urls = {a["source_url"] for a in articles}
        self.titles = [_norm(a["source_title"]) for a in articles[:600]]

    def is_duplicate(self, item: dict) -> bool:
        if item["id"] in self.ids or item["source_url"] in self.urls:
            return True
        t = _norm(item["source_title"])
        for other in self.titles:
            if abs(len(t) - len(other)) < 40 and \
               SequenceMatcher(None, t, other).ratio() >= config.DUPLICATE_TITLE_SIMILARITY:
                return True
        return False

    def add(self, item: dict) -> None:
        self.ids.add(item["id"])
        self.urls.add(item["source_url"])
        self.titles.insert(0, _norm(item["source_title"]))
