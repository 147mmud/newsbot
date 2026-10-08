"""
Publish processed articles to a self-hosted WordPress site via the REST API.
Auth: WordPress Application Passwords (built in since WP 5.6, no plugin).
"""
from __future__ import annotations

import html
import logging

import requests

import config

log = logging.getLogger("wordpress")


class WordPress:
    def __init__(self):
        if not (config.WP_URL and config.WP_USER and config.WP_APP_PASSWORD):
            raise RuntimeError("WP_URL, WP_USER and WP_APP_PASSWORD must be set")
        self.api = f"{config.WP_URL}/wp-json/wp/v2"
        self.s = requests.Session()
        self.s.auth = (config.WP_USER, config.WP_APP_PASSWORD)
        self.s.headers["User-Agent"] = config.USER_AGENT
        self._cat_cache: dict[str, int] = {}
        self._tag_cache: dict[str, int] = {}

    # ---- taxonomy helpers -------------------------------------------------
    def _term_id(self, kind: str, name: str, cache: dict) -> int | None:
        key = name.lower()
        if key in cache:
            return cache[key]
        r = self.s.get(f"{self.api}/{kind}", params={"search": name, "per_page": 20}, timeout=20)
        r.raise_for_status()
        for term in r.json():
            if html.unescape(term["name"]).lower() == key:
                cache[key] = term["id"]
                return term["id"]
        r = self.s.post(f"{self.api}/{kind}", json={"name": name}, timeout=20)
        if r.status_code == 400 and r.json().get("code") == "term_exists":
            cache[key] = r.json()["data"]["term_id"]
            return cache[key]
        r.raise_for_status()
        cache[key] = r.json()["id"]
        return cache[key]

    # ---- content ----------------------------------------------------------
    @staticmethod
    def render_html(a: dict) -> str:
        """Gutenberg-friendly HTML. Ads are inserted by your WP ad plugin/theme
        (e.g. 'Ad Inserter' free plugin) - see README."""
        e = html.escape
        parts = []
        if a.get("key_points"):
            parts.append("<h2>Key Points</h2><ul>" +
                         "".join(f"<li>{e(k)}</li>" for k in a["key_points"]) + "</ul>")
        for b in a["body"]:
            if b["type"] == "p":
                parts.append(f"<p>{e(b['text'])}</p>")
            elif b["type"] == "h2":
                parts.append(f"<h2>{e(b['text'])}</h2>")
            elif b["type"] == "quote":
                parts.append(f"<blockquote><p>{e(b['text'])}</p><cite>{e(b['cite'])}</cite></blockquote>")
        parts.append(
            f'<p class="source-credit"><em>Source: <a href="{e(a["source_url"])}" '
            f'target="_blank" rel="noopener nofollow">{e(a["source"])}</a></em></p>')
        return "\n".join(parts)

    def publish(self, a: dict) -> int | None:
        try:
            cat_id = self._term_id("categories", config.CATEGORIES[a["category"]], self._cat_cache)
            tag_ids = [t for t in (self._term_id("tags", t, self._tag_cache) for t in a["tags"][:6]) if t]
            payload = {
                "title": a["headline"],
                "slug": a["slug"],
                "content": self.render_html(a),
                "excerpt": a["meta_description"],
                "status": config.WP_POST_STATUS,
                "categories": [cat_id],
                "tags": tag_ids,
                "date_gmt": a["published"][:19],
            }
            r = self.s.post(f"{self.api}/posts", json=payload, timeout=30)
            r.raise_for_status()
            post_id = r.json()["id"]
            log.info("WP post %s: %s", post_id, a["headline"])
            return post_id
        except Exception as exc:
            log.error("WordPress publish failed for %s: %s", a["slug"], exc)
            return None
