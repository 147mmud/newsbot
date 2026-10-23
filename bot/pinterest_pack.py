"""Daily Pinterest PIN PACK for manual upload (no Pinterest API needed).

    python -m bot.pinterest_pack

Writes ./pin_pack/: NN_<category>.jpg (1000x1500) plus pins.txt with the title,
description, link and board for each Pin. The workflow uploads it as one zip.
"""
from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta

from bot import facebook as fb
from bot import pinterest_fb as P

log = logging.getLogger("pinpack")
OUT = fb.ROOT / "pin_pack"
N_NEWS = int(os.getenv("PIN_PACK_NEWS", "8"))
N_SCORES = int(os.getenv("PIN_PACK_SCORES", "3"))
MAX_AGE_H = int(os.getenv("PIN_PACK_AGE_HOURS", "30"))


def run() -> int:
    t = fb.now()
    OUT.mkdir(exist_ok=True)
    articles = fb.load_json(fb.ARTICLES, [])
    cutoff = t - timedelta(hours=MAX_AGE_H)
    items, fake_ledger, per_cat = [], {}, {}
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if len([i for i in items if i["kind"] == "news"]) >= N_NEWS:
            break
        if fb.eligible(a, cutoff) or per_cat.get(a.get("category"), 0) >= 3:
            continue
        if any(fb.similarity(a, i["a"]) >= fb.DUP_THRESHOLD for i in items if i["kind"] == "news"):
            continue
        img = P.pin_image(a)
        if not img:
            continue
        per_cat[a.get("category")] = per_cat.get(a.get("category"), 0) + 1
        items.append({"kind": "news", "a": a, "img": img, "cat": a.get("category", "world"),
                      "title": a["headline"][:100], "desc": P.description(a), "link": fb.article_url(a)})
    for _ in range(N_SCORES):
        s = P.score_pick(fake_ledger, t)
        if not s:
            break
        fake_ledger[s["key"]] = {"status": "posted"}
        items.append({"kind": "score", "img": s["img"], "cat": "sports", "title": s["title"], "desc": s["desc"], "link": P.SITE + "/"})
    lines = []
    for i, it in enumerate(items, 1):
        name = f"{i:02d}_{it['kind']}_{it['cat']}.jpg"
        (OUT / name).write_bytes(it["img"])
        lines.append(f"=== {name}\nBOARD: {P.BOARDS.get(it['cat'], P.BOARDS['world'])}\nTITLE: {it['title']}\n"
                     f"LINK: {it['link']}\nDESCRIPTION: {it['desc']}\n")
    (OUT / "pins.txt").write_text("\n".join(lines) or "No stories found.", encoding="utf-8")
    log.info("pin pack: %d pins (%d news, %d scores)", len(items), len([i for i in items if i["kind"] == "news"]),
             len([i for i in items if i["kind"] == "score"]))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s")
    sys.exit(run())
