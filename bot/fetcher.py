"""Fetch and normalise RSS/Atom items from the configured feeds."""
from __future__ import annotations

import calendar
import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import feedparser
import requests
from bs4 import BeautifulSoup

import config

log = logging.getLogger("fetcher")

TRACKING_PARAMS = re.compile(r"^(utm_|at_|cmp|ns_|ito|ref|smid|partner)", re.I)


def canonical_url(url: str) -> str:
    """Strip tracking params / fragments so the same story dedupes."""
    parts = urlsplit(url.strip())
    query = "&".join(
        q for q in parts.query.split("&") if q and not TRACKING_PARAMS.match(q)
    )
    return urlunsplit((parts.scheme or "https", parts.netloc.lower(), parts.path, query, ""))


def item_id(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:16]


def html_to_text(html: str) -> str:
    if not html:
        return ""
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text)
    # Common feed boilerplate
    text = re.sub(r"(Continue reading\.*|Read more\.*|The post .* appeared first on .*)$", "", text, flags=re.I)
    return text.strip()


def _published(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc)
    return datetime.now(timezone.utc)


def _image(entry) -> str | None:
    """Best thumbnail the feed itself provides (no page scraping)."""
    candidates = []
    for m in entry.get("media_content", []) or []:
        if m.get("url") and (m.get("medium") in (None, "image") or "image" in m.get("type", "")):
            candidates.append((int(m.get("width") or 0), m["url"]))
    for m in entry.get("media_thumbnail", []) or []:
        if m.get("url"):
            candidates.append((int(m.get("width") or 0), m["url"]))
    for enc in entry.get("enclosures", []) or []:
        if "image" in enc.get("type", "") and enc.get("href"):
            candidates.append((0, enc["href"]))
    if not candidates:
        # Some feeds (TechCrunch, Verge) embed an <img> in the description/content
        html = ""
        if entry.get("content"):
            html = entry["content"][0].get("value", "")
        html = html or entry.get("summary", "")
        img = BeautifulSoup(html, "html.parser").find("img") if html else None
        if img and img.get("src", "").startswith("http"):
            candidates.append((0, img["src"]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0], reverse=True)
    return candidates[0][1]


def fetch_feed(feed: tuple) -> list[dict]:
    url, source, category_hint = feed
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT,
                            headers={"User-Agent": config.USER_AGENT})
        resp.raise_for_status()
    except Exception as exc:  # network errors must never kill the run
        log.warning("Feed failed %s: %s", url, exc)
        return []
    parsed = feedparser.parse(resp.content)
    items = []
    cutoff = time.time() - config.MAX_ARTICLE_AGE_HOURS * 3600
    for entry in parsed.entries[: config.MAX_PER_FEED]:
        link = entry.get("link")
        title = html_to_text(entry.get("title", ""))
        if not link or not title:
            continue
        published = _published(entry)
        if published.timestamp() < cutoff:
            continue
        summary_html = entry.get("summary", "")
        if not summary_html and entry.get("content"):
            summary_html = entry["content"][0].get("value", "")
        tags = [t.get("term") for t in entry.get("tags", []) if t.get("term")]
        items.append({
            "id": item_id(link),
            "source_url": canonical_url(link),
            "source": source,
            "source_title": title,
            "source_summary": html_to_text(summary_html)[:1500],
            "source_tags": tags[:10],
            "category_hint": category_hint,
            "image": _image(entry),
            "published": published.isoformat(),
        })
    log.info("%-22s %-55s %d items", source, url[:55], len(items))
    return items


def fetch_all(feeds=None) -> list[dict]:
    feeds = feeds or config.FEEDS
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = pool.map(fetch_feed, feeds)
    items = [i for batch in results for i in batch]
    items.sort(key=lambda i: i["published"], reverse=True)
    return items
