"""Render the static site into ./public (deployed to GitHub Pages / Netlify / Vercel)."""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from xml.sax.saxutils import escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

import config
from bot import scores

log = logging.getLogger("builder")

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "public"
PER_PAGE = 24

try:
    from zoneinfo import ZoneInfo
    DISPLAY_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    DISPLAY_TZ = timezone.utc

BASE_PATH = (urlsplit(config.SITE_URL).path.rstrip("/") + "/") or "/"


def url(path: str = "") -> str:
    """Site-relative URL that works on GitHub *project* pages (/repo/...)."""
    return BASE_PATH + path.lstrip("/")


def abs_url(path: str = "") -> str:
    return config.SITE_URL + "/" + path.lstrip("/")


def fmt_date(iso: str) -> str:
    d = datetime.fromisoformat(iso).astimezone(DISPLAY_TZ)
    tz = "ET" if DISPLAY_TZ is not timezone.utc else "UTC"
    return d.strftime("%b %-d, %Y · %-I:%M %p ") + tz


def ad(name: str) -> str:
    return config.ADS.get(name, "") or ""


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(ROOT / "templates"),
                      autoescape=select_autoescape(["html", "xml"]),
                      trim_blocks=True, lstrip_blocks=True)
    env.globals.update(url=url, abs_url=abs_url, ad=ad, site=config,
                       categories=config.CATEGORIES, year=datetime.now().year)
    env.filters["date"] = fmt_date
    env.filters["tojson_safe"] = lambda o: json.dumps(o, ensure_ascii=False).replace("</", "<\\/")
    return env


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _paginate(items, per_page):
    pages = [items[i:i + per_page] for i in range(0, len(items), per_page)] or [[]]
    return pages


def build(articles: list[dict], with_scores: bool = True) -> None:
    env = _env()
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    shutil.copytree(ROOT / "static", OUT / "static")

    by_cat = {c: [a for a in articles if a["category"] == c] for c in config.CATEGORIES}
    trending = articles[:6]

    # --- Home ---------------------------------------------------------------
    _write(OUT / "index.html", env.get_template("index.html").render(
        page="home", articles=articles[:40], by_cat=by_cat, trending=trending,
        canonical=abs_url(), title=f"{config.SITE_NAME} – {config.SITE_TAGLINE}",
        description=config.SITE_TAGLINE))

    # --- Category pages (paginated) ----------------------------------------
    tpl = env.get_template("category.html")
    for slug, name in config.CATEGORIES.items():
        pages = _paginate(by_cat[slug], PER_PAGE)
        for i, chunk in enumerate(pages, 1):
            path = f"category/{slug}/" + ("" if i == 1 else f"page/{i}/")
            _write(OUT / path / "index.html", tpl.render(
                page=slug, cat_slug=slug, cat_name=name, articles=chunk, trending=trending,
                page_num=i, total_pages=len(pages), canonical=abs_url(path),
                title=f"{name} News Today – Latest Headlines | {config.SITE_NAME}",
                description=f"Latest {name.lower()} news and breaking headlines from the US, UK, Canada and Europe."))

    # --- Articles -----------------------------------------------------------
    tpl = env.get_template("article.html")
    for a in articles:
        related = [r for r in by_cat[a["category"]] if r["id"] != a["id"]][:4]
        path = f"news/{a['slug']}/"
        _write(OUT / path / "index.html", tpl.render(
            page=a["category"], a=a, related=related, trending=trending,
            canonical=abs_url(path), title=f"{a['headline']} | {config.SITE_NAME}",
            description=a["meta_description"], og_image=a.get("image") if config.USE_FEED_IMAGES else None))

    # --- Static pages -------------------------------------------------------
    for name in ("about", "privacy", "contact"):
        _write(OUT / name / "index.html", env.get_template(f"{name}.html").render(
            page=name, trending=trending, canonical=abs_url(f"{name}/"),
            title=f"{name.title()} | {config.SITE_NAME}", description=config.SITE_TAGLINE))
    _write(OUT / "404.html", env.get_template("404.html").render(
        page="404", trending=trending, canonical=abs_url(), title="Page not found", description=""))

    _build_feeds(articles)
    if with_scores:
        scores.snapshot(OUT / "data" / "scores.json")
    else:
        _write(OUT / "data" / "scores.json", json.dumps({"updated": None, "sports": {}}))
    _write(OUT / ".nojekyll", "")
    log.info("Built %d articles into %s", len(articles), OUT)


def _build_feeds(articles: list[dict]) -> None:
    now = datetime.now(timezone.utc)
    indexable = [a for a in articles if a.get("index", True)]

    # sitemap.xml
    urls = [abs_url()] + [abs_url(f"category/{c}/") for c in config.CATEGORIES]
    rows = [f"<url><loc>{escape(u)}</loc><changefreq>hourly</changefreq></url>" for u in urls]
    rows += [f"<url><loc>{escape(abs_url('news/' + a['slug'] + '/'))}</loc>"
             f"<lastmod>{a['published'][:10]}</lastmod></url>" for a in indexable]
    _write(OUT / "sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + "".join(rows) + "</urlset>")

    # Google News sitemap (articles from the last 48h only, per Google's spec)
    recent = [a for a in indexable if datetime.fromisoformat(a["published"]) > now - timedelta(hours=48)]
    rows = [
        f"<url><loc>{escape(abs_url('news/' + a['slug'] + '/'))}</loc><news:news>"
        f"<news:publication><news:name>{escape(config.SITE_NAME)}</news:name>"
        f"<news:language>en</news:language></news:publication>"
        f"<news:publication_date>{a['published']}</news:publication_date>"
        f"<news:title>{escape(a['headline'])}</news:title></news:news></url>"
        for a in recent[:1000]]
    _write(OUT / "news-sitemap.xml", '<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
           'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">' + "".join(rows) + "</urlset>")

    # RSS feed of our own site
    items = "".join(
        f"<item><title>{escape(a['headline'])}</title>"
        f"<link>{escape(abs_url('news/' + a['slug'] + '/'))}</link>"
        f"<guid isPermaLink=\"false\">{a['id']}</guid>"
        f"<pubDate>{datetime.fromisoformat(a['published']).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"<category>{escape(config.CATEGORIES[a['category']])}</category>"
        f"<description>{escape(a['meta_description'])}</description></item>"
        for a in articles[:50])
    _write(OUT / "feed.xml", '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           f"<title>{escape(config.SITE_NAME)}</title><link>{escape(abs_url())}</link>"
           f"<description>{escape(config.SITE_TAGLINE)}</description><language>en-us</language>"
           f"{items}</channel></rss>")

    _write(OUT / "robots.txt", f"User-agent: *\nAllow: /\n\nSitemap: {abs_url('sitemap.xml')}\n"
                               f"Sitemap: {abs_url('news-sitemap.xml')}\n")
