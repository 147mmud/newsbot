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
        dedupe = store.Deduper(articles)
        for item in raw:
            if len(new) >= config.MAX_NEW_PER_RUN:
                break
            if dedupe.is_duplicate(item):
                continue
            dedupe.add(item)
            art = processor.process(item)
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
