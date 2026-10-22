"""Pinterest auto-pinner: one tall Pin per run (news story photo + headline).

    python -m bot.pinterest_fb        # what the hourly GitHub Actions job runs

* PREVIEW unless PINTEREST_ENABLED=true: renders the Pin image to ./pins_out/
  (workflow artifact) and posts nothing. Ledger untouched.
* LIVE: PINTEREST_ENABLED=true + PINTEREST_APP_ID + PINTEREST_APP_SECRET.
  The refresh token lives in data/pinterest_auth.enc (encrypted with the app secret).
* Ledger keys "pn:<article id>" in the shared data/fb_posted.json, same
  reserve -> push -> post -> record flow as Facebook. No "link" field is stored
  so the Facebook reconciler never touches these entries.
* Trial access: Pins are only visible to the account owner until Standard access.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

import requests
from PIL import Image, ImageDraw
from cryptography.fernet import Fernet

from bot import facebook as fb
from bot import reels as R
from bot.site_builder import bigimg

log = logging.getLogger("pinterest")

APP_ID = os.getenv("PINTEREST_APP_ID", "").strip()
APP_SECRET = "".join(os.getenv("PINTEREST_APP_SECRET", "").split())
ENABLED = os.getenv("PINTEREST_ENABLED", "").strip().lower() == "true"
MAX_PER_DAY = int(os.getenv("PINTEREST_MAX_PER_DAY", "8"))
MAX_AGE_H = int(os.getenv("PINTEREST_MAX_AGE_HOURS", "20"))
API = "https://api.pinterest.com/v5"
AUTH_FILE = fb.ROOT / "data" / "pinterest_auth.enc"
OUT = fb.ROOT / "pins_out"
SITE = fb.config.SITE_URL.rstrip("/")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}

BOARDS = {"politics": "Politics News", "world": "World News", "business": "Business & Finance News",
          "tech": "Tech News", "sports": "Sports News & Scores"}
BOARD_DESC = {"politics": "Politics headlines from the US, UK, Canada and Europe.",
              "world": "World news summaries from trusted publishers.",
              "business": "Business, markets and economy news.",
              "tech": "Technology, AI and gadgets news.",
              "sports": "Sports news, live scores and results."}
CAT_COLOR = {"politics": (29, 78, 216), "world": (15, 118, 110), "business": (180, 83, 9),
             "tech": (124, 58, 237), "sports": (21, 128, 61)}
TAGS = {"politics": "#PoliticsNews #USNews #UKNews", "world": "#WorldNews #BreakingNews #GlobalNews",
        "business": "#BusinessNews #Economy #Markets", "tech": "#TechNews #AI #Technology",
        "sports": "#SportsNews #LiveScore #Sports"}


# --------------------------------------------------------------------------- auth + API
def access_token() -> str:
    key = base64.urlsafe_b64encode(hashlib.sha256(APP_SECRET.encode()).digest())
    refresh = json.loads(Fernet(key).decrypt(AUTH_FILE.read_bytes()))["refresh_token"]
    r = requests.post(f"{API}/oauth/token", auth=(APP_ID, APP_SECRET), timeout=30,
                      data={"grant_type": "refresh_token", "refresh_token": refresh})
    body = r.json() if r.content else {}
    if r.status_code != 200 or "access_token" not in body:
        raise RuntimeError(f"token refresh failed {r.status_code}: {body.get('message')}")
    return body["access_token"]


def api(method, path, token, **kw):
    r = requests.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, timeout=60, **kw)
    body = r.json() if r.content else {}
    if r.status_code >= 300:
        raise RuntimeError(f"Pinterest {r.status_code} on {method} {path}: code={body.get('code')} {str(body.get('message'))[:200]}")
    return body


def board_id(token, cat) -> str:
    name = BOARDS.get(cat, BOARDS["world"])
    bookmark = None
    while True:
        res = api("GET", "/boards", token, params={"page_size": 100, **({"bookmark": bookmark} if bookmark else {})})
        for b in res.get("items", []):
            if b["name"].lower() == name.lower():
                return b["id"]
        bookmark = res.get("bookmark")
        if not bookmark:
            break
    log.info("creating board %s", name)
    res = api("POST", "/boards", token, json={"name": name, "description": BOARD_DESC.get(cat, ""), "privacy": "PUBLIC"})
    return res["id"]


# --------------------------------------------------------------------------- pin image (1000x1500)
def pin_image(a) -> bytes | None:
    try:
        url = re.sub(r"(-mediumSquareAt3X|-thumbStandard)\.", "-superJumbo.", bigimg(a["image"])) if "nyt.com" in a["image"] else bigimg(a["image"])
        r = requests.get(url, headers=UA, timeout=25)
        if r.status_code != 200 and url != a["image"]:
            r = requests.get(a["image"], headers=UA, timeout=25)
        src = Image.open(io.BytesIO(r.content)).convert("RGB")
    except Exception as exc:
        log.warning("image fetch failed for %s: %s", a["id"], exc)
        return None
    if min(src.size) < 300:
        return None
    W, H, TOP = 1000, 1500, 900
    sw, sh = src.size
    k = max(W / sw, TOP / sh)
    src = src.resize((int(sw * k) + 1, int(sh * k) + 1), Image.LANCZOS)
    x, y = (src.width - W) // 2, (src.height - TOP) // 2
    img = Image.new("RGB", (W, H), (11, 29, 51))
    img.paste(src.crop((x, y, x + W, y + TOP)), (0, 0))
    d = ImageDraw.Draw(img, "RGBA")
    for i in range(140):                                        # soft fade photo -> panel
        d.rectangle((0, TOP - 140 + i, W, TOP - 139 + i), fill=(11, 29, 51, int(255 * (i / 139) ** 1.6)))
    cat = a.get("category", "world")
    col = CAT_COLOR.get(cat, (200, 16, 46))
    d.rectangle((0, TOP, W, TOP + 8), fill=col + (255,))
    label = {"politics": "POLITICS", "world": "WORLD NEWS", "business": "BUSINESS", "tech": "TECH", "sports": "SPORTS"}.get(cat, "NEWS")
    f_chip = R.font(34)
    cw = d.textlength(label, font=f_chip) + 48
    d.rounded_rectangle((60, TOP + 36, 60 + cw, TOP + 94), 10, fill=col + (255,))
    d.text((84, TOP + 44), label, font=f_chip, fill=(255, 255, 255))
    head = a["headline"].strip()
    size = 76
    while size > 44:
        f = R.font(size, serif=True)
        lines = R.wrap(d, head, f, W - 120)
        if len(lines) <= 4:
            break
        size -= 4
    f = R.font(size, serif=True)
    lines = R.wrap(d, head, f, W - 120)[:4]
    ty = TOP + 120
    for ln in lines:
        d.text((60, ty), ln, font=f, fill=(255, 255, 255))
        ty += int(size * 1.22)
    d.rectangle((0, H - 96, W, H), fill=(6, 13, 24, 255))
    d.text((60, H - 70), "thepressandscore.com", font=R.font(36), fill=(255, 255, 255))
    d.text((W - 60 - d.textlength("READ THE STORY  ›", font=R.font(30)), H - 66), "READ THE STORY  ›", font=R.font(30), fill=(255, 196, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def description(a) -> str:
    base = (a.get("meta_description") or "").strip()
    text = f"{base} Read the full story at thepressandscore.com. {TAGS.get(a.get('category'), '#News')}"
    return text[:780]


# --------------------------------------------------------------------------- selection
def pick(articles, ledger, t):
    cutoff = t - timedelta(hours=MAX_AGE_H)
    recent = [a for a in articles if f"pn:{a['id']}" in ledger and ledger[f"pn:{a['id']}"].get("status") in ("posted", "posting", "needs_review")]
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if f"pn:{a['id']}" in ledger or fb.eligible(a, cutoff):
            continue
        if any(fb.similarity(a, r) >= fb.DUP_THRESHOLD for r in recent):
            continue
        return a
    return None


def pins_today(ledger, t):
    day = t.strftime("%Y-%m-%d")
    return sum(1 for k, v in ledger.items() if k.startswith("pn:") and v.get("status") in ("posted", "posting")
               and str(v.get("ts", "")).startswith(day))


# --------------------------------------------------------------------------- score results
MAX_SCORE_PER_DAY = int(os.getenv("PINTEREST_MAX_SCORE_PER_DAY", "3"))


def score_pick(ledger, t):
    """Newest finished big game not yet pinned -> dict(key, title, desc, img) or None."""
    try:
        from bot import scores_fb as S
        events = []
        for off in (-1, 0):
            events += S.fetch_day(t + timedelta(days=off))
    except Exception as exc:
        log.warning("score fetch failed: %s", exc)
        return None
    best = None
    for e in events:
        key = f"pn:sc:{e['league']}:{e['id']}"
        if key in ledger or e["state"] != "post" or not e["completed"] or S.kickoff(e) < t - timedelta(hours=11):
            continue
        v = S.verdict(e)
        if not v.get("label") and not S.is_big(e):
            continue
        rank = e["prio"] * 2 + v["excite"] * 3
        if not best or rank > best[0]:
            best = (rank, e, v, key)
    if not best:
        return None
    _, e, v, key = best
    a, b = e["teams"]
    card = S.card_final(e, v).convert("RGB").resize((1000, 1000), Image.LANCZOS)
    img = Image.new("RGB", (1000, 1500), (11, 29, 51))
    img.paste(card, (0, 180))
    d = ImageDraw.Draw(img)
    d.text((60, 70), "FULL TIME  |  " + e["label"].upper(), font=R.font(44), fill=(255, 255, 255))
    d.text((60, 1280), f"{a['name']} {a['score']}-{b['score']} {b['name']}", font=R.font(48, serif=True), fill=(255, 255, 255))
    d.rectangle((0, 1404, 1000, 1500), fill=(6, 13, 24, 255))
    d.text((60, 1430), "Live scores: thepressandscore.com", font=R.font(36), fill=(255, 255, 255))
    buf = io.BytesIO(); img.save(buf, "JPEG", quality=88)
    title = f"{e['label']}: {a['name']} {a['score']}-{b['score']} {b['name']}"
    desc = f"Full-time result. {title}. Live scores for every game at thepressandscore.com #{e['label'].replace(' ', '').replace('.', '')} #LiveScores #Sports"
    return {"key": key, "title": title[:100], "desc": desc[:780], "img": buf.getvalue()}


def score_pins_today(ledger, t):
    day = t.strftime("%Y-%m-%d")
    return sum(1 for k, v in ledger.items() if k.startswith("pn:sc:") and v.get("status") in ("posted", "posting")
               and str(v.get("ts", "")).startswith(day))


# --------------------------------------------------------------------------- main
def run() -> int:
    t = fb.now()
    articles = fb.load_json(fb.ARTICLES, [])
    ledger = fb.load_json(fb.LEDGER, {}) or {}
    live = ENABLED and APP_ID and APP_SECRET and AUTH_FILE.exists() and fb.LEDGER.exists()
    if live and pins_today(ledger, t) >= MAX_PER_DAY:
        log.info("Daily Pin limit reached (%d).", MAX_PER_DAY)
        return 0
    # park stale reservations from a crashed run
    stale = [k for k, v in ledger.items() if k.startswith("pn:") and v.get("status") == "posting"
             and fb.parse(v["ts"]) < t - timedelta(minutes=30)]
    for k in stale:
        ledger[k].update(status="needs_review", ts=fb.iso(t), note="pin outcome unknown")
    if stale and live:
        ledger = fb.push_ledger(ledger, "pinterest: park stale pins") or ledger

    item = None
    if score_pins_today(ledger, t) < MAX_SCORE_PER_DAY:
        item = score_pick(ledger, t)
        if item:
            item.update(cat="sports", link=SITE + "/")
    if not item:
        a = None
        img = None
        for _ in range(6):                                      # skip stories whose image can't be used
            a = pick(articles, ledger, t)
            if not a:
                break
            img = pin_image(a)
            if img:
                break
            ledger[f"pn:{a['id']}"] = {"status": "skipped", "ts": fb.iso(t), "note": "image unusable"}
            a = None
        if a and img:
            item = {"key": f"pn:{a['id']}", "title": a["headline"][:100], "desc": description(a), "img": img,
                    "cat": a.get("category"), "link": fb.article_url(a), "alt": a["headline"][:480]}
    if not item:
        log.info("No story to pin this run.")
        return 0

    key, img = item["key"], item["img"]
    log.info("%s Pin: %s", "LIVE" if live else "PREVIEW", item["title"])
    if not live:
        OUT.mkdir(exist_ok=True)
        stem = key.replace("pn:", "pin_").replace(":", "_")
        (OUT / f"{stem}.jpg").write_bytes(img)
        (OUT / f"{stem}.txt").write_text(f"{item['title']}\n\n{item['desc']}\n{item['link']}", encoding="utf-8")
        log.info("PREVIEW rendered %s", stem)
        return 0

    ledger[key] = {"status": "posting", "ts": fb.iso(fb.now()), "title": item["title"]}
    merged = fb.push_ledger(ledger, f"pinterest: reserve {key}")
    if merged is None:
        log.error("Could not push reservation; NOT pinning")
        return 1
    ledger = merged
    if ledger[key].get("status") != "posting":
        return 0
    try:
        token = access_token()
        bid = board_id(token, item["cat"])
        res = api("POST", "/pins", token, json={
            "board_id": bid, "title": item["title"], "description": item["desc"],
            "alt_text": item.get("alt", item["title"])[:480], "link": item["link"],
            "media_source": {"source_type": "image_base64", "content_type": "image/jpeg",
                             "data": base64.b64encode(img).decode()}})
        ledger[key].update(status="posted", ts=fb.iso(fb.now()), post_id=res.get("id"))
        log.info("PINNED %s -> %s", item["title"], res.get("id"))
    except Exception as exc:
        log.error("Pin failed: %s", exc)
        ledger[key].update(status="failed", ts=fb.iso(fb.now()), note=str(exc)[:150])
        fb.push_ledger(ledger, f"pinterest: failed {key}")
        return 1
    return 0 if fb.push_ledger(ledger, f"pinterest: pinned {key}") is not None else 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s")
    sys.exit(run())
