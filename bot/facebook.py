"""Facebook Page auto-poster with a duplicate-proof ledger.

    python -m bot.facebook            # what the GitHub Actions job runs

Safety model
------------
* DRY RUN unless FB_ENABLED=true AND FB_PAGE_ID + FB_PAGE_TOKEN are set:
  it only logs what it would post and writes nothing.
* Ledger: data/fb_posted.json  {article_id: {status, ts, post_id?, link, title}}
  committed to the repo, so it survives every run, failure and re-run.
* Reserve -> push -> post -> record -> push. If the reservation can't be
  pushed (another run got there first, or git fails) nothing is posted.
* A reservation left behind by a crashed run is never retried blindly: the
  Page's recent posts are checked for the link first. If that check can't be
  done, the entry is parked as "needs_review" for a human.
* First live run (no ledger yet) only records the current stories as
  "bootstrap" and posts nothing, so the Page is never flooded.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import config
from bot import fetcher

log = logging.getLogger("facebook")

ROOT = Path(__file__).resolve().parent.parent
ARTICLES = ROOT / "data" / "articles.json"
LEDGER = ROOT / "data" / "fb_posted.json"

ENABLED = os.getenv("FB_ENABLED", "").strip().lower() == "true"
PAGE_ID = os.getenv("FB_PAGE_ID", "").strip()
TOKEN = os.getenv("FB_PAGE_TOKEN", "").strip()          # GitHub Secret only
GRAPH = "https://graph.facebook.com/" + os.getenv("FB_GRAPH_VERSION", "v26.0")
MAX_PER_RUN = int(os.getenv("FB_MAX_PER_RUN", "1"))
MAX_AGE_H = int(os.getenv("FB_MAX_AGE_HOURS", "6"))     # only fresh stories
DUP_WINDOW_H = int(os.getenv("FB_DUP_WINDOW_HOURS", "48"))
DUP_THRESHOLD = float(os.getenv("FB_DUP_THRESHOLD", "0.4"))
STALE_MIN = 30                                          # minutes before a reservation counts as abandoned

# status order: a merge always keeps the most advanced state
RANK = {"bootstrap": 5, "posted": 5, "skipped": 4, "needs_review": 3, "failed": 2, "posting": 1}


# --------------------------------------------------------------------------- helpers
def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_ledger(ledger: dict) -> None:
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")


def merge_ledgers(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if k not in out or RANK.get(v.get("status"), 0) > RANK.get(out[k].get("status"), 0):
            out[k] = v
    return out


def article_url(a: dict) -> str:
    return config.SITE_URL.rstrip("/") + "/news/" + a["slug"] + "/"


# --------------------------------------------------------------------------- near-duplicates
_STOP = set("""about after again against ahead also amid among back been before being between
both could does down during each from have into just more most much next over says said such
than that their them then there these they this those through under until were what when where
which while will with would year years your news""".split())


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("’", "'").replace("'s ", " "))
    out = set()
    for w in words:
        if len(w) < 4 or w in _STOP:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)   # crude plural strip
    return out


def similarity(a: dict, b: dict) -> float:
    """Overlap coefficient of headline+summary keywords (same event, other source)."""
    ta = tokens(a.get("headline", "") + " " + a.get("meta_description", ""))
    tb = tokens(b.get("headline", "") + " " + b.get("meta_description", ""))
    if len(ta) < 4 or len(tb) < 4:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


# --------------------------------------------------------------------------- selection
def eligible(a: dict, cutoff: datetime) -> str | None:
    """Return None if the article may be posted, else the reason it may not."""
    if not a.get("image") or fetcher.is_bad_image(a["image"]):
        return "no image"
    if not a.get("index", True):
        return "noindex"
    if parse(a["published"]) < cutoff:
        return "too old"
    return None


def pick(articles: list[dict], ledger: dict, t: datetime) -> list[dict]:
    cutoff = t - timedelta(hours=MAX_AGE_H)
    dup_since = t - timedelta(hours=DUP_WINDOW_H)
    by_id = {a["id"]: a for a in articles}
    recent = [by_id[k] for k, v in ledger.items()
              if v.get("status") in ("posted", "posting", "needs_review") and k in by_id
              and parse(v["ts"]) >= dup_since]
    chosen = []
    for a in sorted(articles, key=lambda x: x["published"], reverse=True):
        if a["id"] in ledger:
            continue
        reason = eligible(a, cutoff)
        if reason:
            continue
        twin = next((r for r in recent + chosen
                     if similarity(a, r) >= DUP_THRESHOLD), None)
        if twin:
            log.info("skip near-duplicate: %s  ~  %s", a["headline"], twin["headline"])
            continue
        chosen.append(a)
        if len(chosen) >= MAX_PER_RUN:
            break
    return chosen


# --------------------------------------------------------------------------- git
def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)


def push_ledger(ledger: dict, message: str) -> dict | None:
    """Merge with origin/main, commit and push. Returns the merged ledger, or
    None if it could not be pushed (caller must then NOT post)."""
    for attempt in range(1, 6):
        git("fetch", "-q", "origin", "main")
        remote = git("show", "origin/main:data/fb_posted.json")
        merged = merge_ledgers(json.loads(remote.stdout) if remote.returncode == 0 and remote.stdout.strip() else {}, ledger)
        git("reset", "-q", "origin/main")
        save_ledger(merged)
        git("add", "data/fb_posted.json")
        if git("diff", "--cached", "--quiet").returncode == 0:
            return merged
        git("commit", "-q", "-m", message)
        if git("push", "-q", "origin", "HEAD:main").returncode == 0:
            return merged
        log.warning("ledger push raced, retrying (%d)", attempt)
        time.sleep(5)
    return None


# --------------------------------------------------------------------------- Graph API
def graph(method: str, path: str, **params) -> dict:
    params["access_token"] = TOKEN
    r = requests.request(method, f"{GRAPH}/{path}", data=params if method == "POST" else None,
                         params=None if method == "POST" else params, timeout=30)
    usage = r.headers.get("X-Business-Use-Case-Usage")
    if usage:
        log.info("Meta BUC usage: %s", usage)
    body = r.json() if r.content else {}
    if r.status_code != 200 or "error" in body:
        err = body.get("error", {})
        # never log the token; Meta error bodies don't contain it
        raise RuntimeError(f"Graph API {r.status_code} code={err.get('code')} {err.get('message', '')[:200]}")
    return body


def page_has_link(link: str) -> bool:
    """True if one of the Page's 50 most recent posts already links to the given link."""
    data = graph("GET", f"{PAGE_ID}/feed", fields="id,attachments{url,unshimmed_url}", limit=50)
    for post in data.get("data", []):
        for att in post.get("attachments", {}).get("data", []):
            if link in (att.get("unshimmed_url"), att.get("url")) or link in (att.get("url") or ""):
                return True
    return False


def message_for(a: dict) -> str:
    return f"{a['headline']}\n\n{a.get('meta_description', '')}".strip()


# --------------------------------------------------------------------------- main
def run() -> int:
    articles = load_json(ARTICLES, [])
    ledger = load_json(LEDGER, None)
    t = now()

    if not (ENABLED and PAGE_ID and TOKEN):
        led = ledger or {}
        picks = pick(articles, led, t)
        log.info("DRY RUN (FB_ENABLED=%s, page id set=%s, token set=%s). Ledger entries: %d",
                 ENABLED, bool(PAGE_ID), bool(TOKEN), len(led))
        if ledger is None:
            log.info("First live run would only bootstrap %d existing stories and post nothing.", len(articles))
        for a in picks:
            log.info("WOULD POST: %s -> %s", a["headline"], article_url(a))
        if not picks:
            log.info("Nothing eligible to post right now.")
        return 0

    # 1. bootstrap: first live run marks everything as seen and posts nothing
    if ledger is None:
        ledger = {a["id"]: {"status": "bootstrap", "ts": iso(t)} for a in articles}
        ok = push_ledger(ledger, f"fb: bootstrap ledger ({len(ledger)} stories)")
        log.info("Bootstrapped ledger with %d stories; nothing posted this run.", len(ledger))
        return 0 if ok is not None else 1

    # keep the ledger small: old finished entries can never become eligible
    # again (stories older than FB_MAX_AGE_HOURS are never posted)
    keep_after = t - timedelta(days=30)
    ledger = {k: v for k, v in ledger.items()
              if v.get("status") in ("posting", "needs_review") or parse(v["ts"]) >= keep_after}

    # 2. reconcile reservations abandoned by a crashed/cancelled run
    changed = False
    for k, v in ledger.items():
        if v.get("status") == "posting" and parse(v["ts"]) < t - timedelta(minutes=STALE_MIN):
            try:
                found = page_has_link(v["link"])
                v.update(status="posted" if found else "failed", ts=iso(t), note="reconciled")
            except Exception as exc:                      # can't verify -> park for a human
                v.update(status="needs_review", ts=iso(t), note=str(exc)[:200])
            changed = True
            log.warning("reconciled %s -> %s", k, v["status"])
    if changed:
        ledger = push_ledger(ledger, "fb: reconcile ledger") or ledger

    # 3. reserve -> post -> record, one story at a time
    picks = pick(articles, ledger, t)
    if not picks:
        log.info("No new story to post this run (ledger: %d entries).", len(ledger))
    for a in picks:
        link = article_url(a)
        ledger[a["id"]] = {"status": "posting", "ts": iso(now()), "link": link, "title": a["headline"]}
        merged = push_ledger(ledger, f"fb: reserve {a['id']}")
        if merged is None:
            log.error("Could not push reservation; NOT posting %s", a["id"])
            return 1
        ledger = merged
        if ledger[a["id"]].get("status") != "posting":      # someone else already handled it
            continue
        try:
            res = graph("POST", f"{PAGE_ID}/feed", link=link, message=message_for(a))
            ledger[a["id"]].update(status="posted", ts=iso(now()), post_id=res.get("id"))
            log.info("POSTED %s -> %s", a["headline"], res.get("id"))
        except Exception as exc:
            # The request may or may not have reached Facebook: leave "posting"
            # so the next run reconciles it against the Page instead of re-posting.
            log.error("Post failed for %s: %s", a["id"], exc)
            push_ledger(ledger, f"fb: post attempt {a['id']}")
            return 1
        if push_ledger(ledger, f"fb: posted {a['id']}") is None:
            log.error("Posted but could not record %s; next run will reconcile it", a["id"])
            return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s")
    sys.exit(run())
