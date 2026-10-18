#!/usr/bin/env python3
"""
NewsBot - fetch RSS -> rewrite/summarise -> categorise -> publish.

Usage:
  python main.py                 # full run (what GitHub Actions does)
  python main.py --build-only    # rebuild the static site from data/ only
  python main.py --feeds-file tests/feeds.txt   # use local/alternate feeds
"""
import argparse
import logging
import sys
import time

import config
from bot import fetcher, processor, store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-10s %(message)s")
log = logging.getLogger("main")


def run(args) -> int:
    articles = store.load()
    log.info("Loaded %d stored articles", len(articles))

    new = []
    if not args.build_only:
        feeds = config.FEEDS
        if args.feeds_file:
            feeds = [tuple((l.split("|") + [None, None])[:3]) for l in open(args.feeds_file).read().split("\n") if l.strip()]
        raw = fetcher.fetch_all(feeds)
        log.info("Fetched %d candidate items", len(raw))
        # Round-robin across sections so one busy feed (e.g. Tech) can't fill
        # the whole per-run quota.
        buckets = {}
        for it in raw:
            buckets.setdefault(it.get("category_hint") or "other", []).append(it)
        raw = []
        while any(buckets.values()):
            for key in list(buckets):
                if buckets[key]:
                    raw.append(buckets[key].pop(0))
        dedupe = store.Deduper(articles)
        for item in raw:
            if len(new) >= config.MAX_NEW_PER_RUN:
                break
            if dedupe.is_duplicate(item):
                continue
            dedupe.add(item)
            art = processor.process(item)
            if (not art.get("image") or fetcher.is_bad_image(art.get("image"))) and config.USE_FEED_IMAGES:
                art["image"] = fetcher.page_image(art["source_url"])
                art["img_checked"] = True
            art["added"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            new.append(art)
            log.info("[%s|%s] %s", art["category"], art["mode"], art["headline"])

    if new and config.PUBLISH_TARGET in ("wordpress", "both"):
        from bot.wordpress import WordPress
        wp = WordPress()
        for art in new:
            art["wp_id"] = wp.publish(art)
        # keep failed posts out of the store so they are retried next run
        failed = [a for a in new if not a.get("wp_id")]
        if failed and config.PUBLISH_TARGET == "wordpress":
            new = [a for a in new if a.get("wp_id")]
            log.warning("%d WordPress posts failed, will retry next run", len(failed))

    # Backfill: give older articles without a picture a try (a few per run)
    if not args.build_only and config.USE_FEED_IMAGES:
        for a in articles:                      # drop tracking pixels saved earlier
            if fetcher.is_bad_image(a.get("image")):
                a["image"] = None
                a.pop("img_checked", None)
        todo = [a for a in articles if not a.get("image") and not a.get("img_checked")][:60]
        for a in todo:
            a["image"] = fetcher.page_image(a["source_url"])
            a["img_checked"] = True
        if todo:
            log.info("Backfilled images: %d/%d found", sum(1 for a in todo if a["image"]), len(todo))

    articles = new + articles
    store.save(articles)
    log.info("Added %d new articles (total %d)", len(new), len(articles))

    if config.PUBLISH_TARGET in ("static", "both"):
        from bot import site_builder
        site_builder.build(store.load(), with_scores=not args.no_scores)
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--build-only", action="store_true")
    p.add_argument("--no-scores", action="store_true", help="skip the scores snapshot")
    p.add_argument("--feeds-file", help="lines of: url|Source Name|category")
    sys.exit(run(p.parse_args()))
