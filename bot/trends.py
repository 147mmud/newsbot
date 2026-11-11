"""Google Trends keyword enrichment.

Reads Google's free "trending now" RSS for high-tier countries, and for every
fresh article keeps ONLY the trending terms that genuinely relate to that
story (the term appears in it, or the trend's own news headlines overlap with
it). Matches are stored on the article as `trends` and reused for the page
keywords and the Facebook / Telegram / Bluesky / X hashtags.

Fail-safe: if the feed can't be reached nothing changes and nothing breaks.
"""
import json, os, re, sys, time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

GEOS = [g for g in os.getenv("TRENDS_GEOS", "US,GB,CA,AU,IE").split(",") if g]
CACHE = Path("data/trends.json")
MAX_AGE_H = float(os.getenv("TRENDS_MAX_AGE_H", "3"))
MAX_PER_ARTICLE = 3
URL = "https://trends.google.com/trending/rss?geo={}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; PressAndScoreBot/1.0)"}
STOP = set("the a an of to in on for and or at by with from is are was were be as it its this that new says say after over "
           "news latest live update today about into out up us not has have had will vs".split())


def _words(s: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", s.lower()) if w not in STOP and len(w) > 2]


def _fetch_geo(geo: str) -> list[dict]:
    r = requests.get(URL.format(geo), headers=UA, timeout=20)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        term = (it.findtext("title") or "").strip()
        if not term:
            continue
        titles = [(e.text or "").strip() for e in it.iter() if e.tag.endswith("news_item_title") and e.text]
        out.append({"term": term, "geo": geo, "news": titles[:4]})
    return out


def load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def refresh(force: bool = False) -> list[dict]:
    """Return current trends; refetch only if the cache is older than MAX_AGE_H."""
    c = load_cache()
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(c["fetched"])
    except Exception:
        age = timedelta(days=99)
    if not force and c.get("items") and age < timedelta(hours=MAX_AGE_H):
        return c["items"]
    items, ok = [], 0
    for g in GEOS:
        try:
            items += _fetch_geo(g); ok += 1
        except Exception as exc:
            print(f"trends {g}: {exc}", file=sys.stderr)
    if not ok:
        return c.get("items", [])
    seen, uniq = set(), []
    for i in items:
        k = i["term"].lower()
        if k not in seen:
            seen.add(k); uniq.append(i)
    try:
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps({"fetched": datetime.now(timezone.utc).isoformat(), "items": uniq}, ensure_ascii=False))
    except Exception:
        pass
    return uniq


def match(article: dict, items: list[dict]) -> list[str]:
    head = article.get("headline", "")
    text = " ".join([head, article.get("meta_description", ""), article.get("summary", "") if isinstance(article.get("summary"), str) else "",
                     " ".join(article.get("tags", []))]).lower()
    hwords = set(_words(head + " " + article.get("meta_description", "")))
    scored = []
    for it in items:
        term = it["term"]
        tl = term.lower()
        tw = _words(term)
        if len(tl) < 3 or not tw:
            continue
        score = 0
        if re.search(r"(?<![a-z0-9])" + re.escape(tl) + r"(?![a-z0-9])", text):
            score = 3                                    # the term itself is in the story
        elif len(tw) >= 2 and all(w in text for w in tw):
            score = 2                                    # every word of a multi-word term is in the story
        else:
            for n in it.get("news", []):                 # the trend's own headlines overlap this story
                if len(hwords & set(_words(n))) >= 3:
                    score = 1; break
        if score:
            scored.append((score, term))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored[:MAX_PER_ARTICLE]]


def hashtag(term: str) -> str:
    s = "".join(w.capitalize() if w.islower() else w for w in re.findall(r"[A-Za-z0-9]+", term))
    return "#" + s if 2 <= len(s) <= 28 else ""


def tags_for(a: dict, limit: int = 3) -> str:
    return " ".join(h for h in (hashtag(t) for t in a.get("trends", [])) if h)[:120] if limit else ""


def enrich(articles: list[dict], hours: int = 24) -> int:
    """Add `trends` to recent articles that haven't been checked yet. Returns how many got matches."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    todo = []
    for a in articles:
        if "trends" in a:
            continue
        try:
            if datetime.fromisoformat(a["published"].replace("Z", "+00:00")) < cutoff:
                continue
        except Exception:
            continue
        todo.append(a)
    if not todo:
        return 0
    items = refresh()
    if not items:
        return 0                                          # feed unreachable: leave unchecked, retry next run
    n = 0
    for a in todo:
        a["trends"] = match(a, items)
        n += bool(a["trends"])
    return n


def main() -> int:
    from bot import store
    arts = store.load()
    n = enrich(arts)
    store.save(arts)
    print(f"trends: {n} articles enriched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
