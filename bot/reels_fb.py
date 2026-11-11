"""Facebook Reels: result, match-day and news-briefing reels.

    python -m bot.reels_fb          # what the hourly GitHub Actions job runs

* PREVIEW (default): renders the reel into ./reels_out/ (kept as a workflow
  artifact) and logs the caption - nothing is posted, ledger untouched.
* LIVE: FB_REELS_ENABLED=true + FB_PAGE_ID + FB_PAGE_TOKEN.
* One reel per run (rendering takes ~1-2 min), FB_REELS_MAX_PER_DAY (default 3).
* Ledger keys start with "rl:" in the shared data/fb_posted.json, same
  reserve -> push -> post -> record flow as the other posters. A reservation
  left by a crash is parked as needs_review, never re-posted blindly.
* Meta Reels API: POST /{page}/video_reels (start) -> rupload -> finish.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import timedelta

import requests

from bot import facebook as fb
from bot import reels as R
from bot import scores_fb as S

log = logging.getLogger("reels_fb")

ENABLED = os.getenv("FB_REELS_ENABLED", "").strip().lower() == "true"
MAX_PER_DAY = int(os.getenv("FB_REELS_MAX_PER_DAY", "8"))
MIN_GAP_MIN = int(os.getenv("FB_REELS_MIN_GAP_MIN", "75"))    # minutes between two reels
FORCE = os.getenv("FB_REELS_FORCE", "").strip().lower()      # result|matchday|news|break|top5|poll|desk: ignore time windows (manual runs)
CAT = {"politics": "Politics", "world": "World News", "business": "Business", "tech": "Tech", "sports": "Sports"}
SITE = S.SITE.replace("https://", "")


def tags(*evs):
    return S.tags(*evs) if evs else ""


# --------------------------------------------------------------------------- candidates
def result_candidate(events, ledger, t):
    best = None
    for e in events:
        if not (e["state"] == "post" and e["completed"] and S.kickoff(e) > t - timedelta(hours=10)):
            continue
        key = f"rl:final:{e['id']}"
        if key in ledger:
            continue
        v = S.verdict(e)
        if not (S.is_big(e) or v.get("label")):
            continue
        rank = e["prio"] * 2 + v["excite"] * 3 + (4 if S.is_big(e) else 0)
        if best is None or rank > best[0]:
            best = (rank, e, v, key)
    if not best:
        return None
    _, e, v, key = best
    a, b = e["teams"]
    q = {"UPSET ALERT": "Did you see this coming? Tell us in the comments.",
         "NAIL-BITER": "Who was your player of the match? Tell us in the comments."}.get(
        v.get("label"), "Rate this game out of ten in the comments.")
    reel = R.result_reel(f"result_{e['id']}", e["label"], {"name": a["name"], "score": a["score"], "color": a["color"]},
                         {"name": b["name"], "score": b["score"], "color": b["color"]}, v.get("label"), v.get("line", ""), q)
    head = {"NAIL-BITER": "😱 WHAT. A. FINISH.", "UPSET ALERT": "🚨 UPSET ALERT!", "STATEMENT WIN": "🔥 STATEMENT WIN.",
            "SHOOTOUT": "💥 WHAT A SHOOTOUT!", "GOAL FEST": "💥 GOAL FEST!", "HONOURS EVEN": "🤝 HONOURS EVEN."}.get(
        v.get("label"), f"{e['emoji']} FULL TIME")
    cap = (f"{head} {a['name']} {a['score']}–{b['score']} {b['name']} | {e['label'].title()}\n"
           f"💬 {q.replace(' Tell us in the comments.', ' 👇').replace(' in the comments.', ' 👇')}\n"
           f"📊 Every score, live: {SITE}\n{S.hashtags('final', e, extra=['#Reels'])}")
    return {"key": key, "kind": "result", "reel": reel, "caption": cap, "title": f"Reel FT {a['name']} v {b['name']}"}


def matchday_candidate(events, ledger, t):
    key = f"rl:today:{t.strftime('%Y-%m-%d')}"
    if key in ledger or (t.hour < 11 and FORCE != "matchday"):
        return None
    today = [e for e in events if e["state"] == "pre" and t <= S.kickoff(e) <= t + timedelta(hours=16) and S.is_big(e)]
    today.sort(key=lambda e: (-e["prio"], S.kickoff(e)))
    top = sorted(today[:4], key=S.kickoff)
    if len(top) < 2:
        return None
    day = t.astimezone(S.UK).strftime("%A")
    games = [(e["label"], e["teams"][0]["name"], e["teams"][1]["name"], *S.times(e)) for e in top]
    reel = R.matchday_reel(f"matchday_{t.strftime('%Y%m%d')}", day, games)
    cap = (f"📅 BIG GAMES TODAY | {day}\n"
           + "\n".join(f"{e['emoji']} {g[1]} vs {g[2]} – {g[3]} / {g[4]}" for e, g in zip(top, games))
           + f"\n🔮 Predict ONE score in the comments 👇\n📊 Live scores: {SITE}\n{S.hashtags('today', *top, extra=['#Reels'])}")
    return {"key": key, "kind": "matchday", "reel": reel, "caption": cap, "title": f"Reel match day {day}"}


def news_candidate(ledger, t):
    key = f"rl:news:{t.strftime('%Y-%m-%d')}"
    if key in ledger or (t.hour < 17 and FORCE != "news"):
        return None
    arts = fb.load_json(fb.ARTICLES, [])
    pick, seen = [], set()
    for a in sorted(arts, key=lambda x: x["published"], reverse=True):
        if fb.parse(a["published"]) < t - timedelta(hours=14) or not a.get("index", True):
            continue
        if a["category"] in seen:
            continue
        if any(fb.similarity(a, p) >= fb.DUP_THRESHOLD for p in pick):
            continue
        seen.add(a["category"])
        pick.append(a)
        if len(pick) == 3:
            break
    if len(pick) < 3:
        return None
    stories = [(CAT.get(a["category"], "News"), a["headline"], a["meta_description"]) for a in pick]
    reel = R.news_reel(f"news_{t.strftime('%Y%m%d')}", stories)
    cap = ("🗞️ TODAY IN 60 SECONDS\n" + "\n".join(f"• {a['headline']}" for a in pick)
           + f"\n💬 Which story matters most to you? 👇\n📰 Full stories: {SITE}\n#BreakingNews #USNews #UKNews #WorldNews #Reels")
    return {"key": key, "kind": "news", "reel": reel, "caption": cap, "title": "Reel today in 60 seconds"}


# --------------------------------------------------------------------------- extra reel styles (news, no photos)
HASH = {"politics": "#Politics #USPolitics", "world": "#WorldNews", "business": "#Business #Markets", "tech": "#Tech #AI",
        "sports": "#Sports"}
DESK = {  # category -> (kicker, title, colour 1, colour 2, seed)
    "politics": ("POLITICS", "POLITICS BRIEF", (29, 78, 216), (127, 29, 29), 11),
    "world": ("WORLD", "WORLD BRIEF", (15, 118, 110), (30, 58, 138), 12),
    "business": ("BUSINESS", "MARKET BRIEF", (180, 83, 9), (21, 128, 61), 13),
    "tech": ("TECH", "TECH BRIEF", (124, 58, 237), (14, 116, 144), 14),
    "sports": ("SPORTS", "SPORTS BRIEF", (21, 128, 61), (29, 78, 216), 15),
}


def fresh_pool(t, hours):
    """Newest-first, indexable, de-duplicated recent stories."""
    arts = fb.load_json(fb.ARTICLES, [])
    pool = []
    for a in sorted(arts, key=lambda x: x["published"], reverse=True):
        if fb.parse(a["published"]) < t - timedelta(hours=hours) or not a.get("index", True):
            continue
        if any(fb.similarity(a, p) >= fb.DUP_THRESHOLD for p in pool):
            continue
        pool.append(a)
    return pool


def breaking_candidate(ledger, t):
    for a in fresh_pool(t, 4):
        key = f"rl:break:{a['id']}"
        pts = [x for x in (a.get("key_points") or []) if x][:3]
        if key in ledger or len(pts) < 2:
            continue
        cat = CAT.get(a["category"], "News")
        reel = R.breaking_reel(f"break_{a['id'][:10]}", cat, a["headline"], pts)
        cap = (f"🚨 BREAKING | {a['headline']}\n" + "\n".join(f"• {p}" for p in pts)
               + f"\n💬 What do you think? 👇\n📰 Full story: {SITE}\n{HASH.get(a['category'], '#News')} #BreakingNews #Reels")
        return {"key": key, "kind": "break", "reel": reel, "caption": cap, "title": f"Reel breaking {a['headline'][:50]}"}


def top5_candidate(ledger, t):
    key = f"rl:top5:{t.strftime('%Y-%m-%d')}-{t.hour // 6}"
    if key in ledger:
        return None
    pool = fresh_pool(t, 12)
    pick, counts = [], {}
    for a in pool:                                  # at most 2 per section, 5 total
        if counts.get(a["category"], 0) >= 2:
            continue
        counts[a["category"]] = counts.get(a["category"], 0) + 1
        pick.append(a)
        if len(pick) == 5:
            break
    if len(pick) < 5:
        return None
    items = [(CAT.get(a["category"], "News"), a["headline"]) for a in pick]
    reel = R.countdown_reel(f"top5_{t.strftime('%Y%m%d%H')}", items)
    ranked = list(zip(range(5, 0, -1), pick))
    cap = ("🔥 TOP 5 STORIES RIGHT NOW\n" + "\n".join(f"{n}. {a['headline']}" for n, a in ranked)
           + f"\n💬 Which one surprised you most? 👇\n📰 Read more: {SITE}\n#News #Trending #USNews #UKNews #Reels")
    return {"key": key, "kind": "top5", "reel": reel, "caption": cap, "title": "Reel top 5 stories"}


def poll_candidate(ledger, t):
    key = f"rl:poll:{t.strftime('%Y-%m-%d')}-{t.hour // 8}"
    if key in ledger:
        return None
    pool = fresh_pool(t, 12)
    if not pool:
        return None
    a = pool[0]
    b = next((x for x in pool[1:] if x["category"] != a["category"]), None)
    if not b:
        return None
    reel = R.poll_reel(f"poll_{t.strftime('%Y%m%d%H')}", (CAT.get(a["category"], "News"), a["headline"]),
                       (CAT.get(b["category"], "News"), b["headline"]))
    cap = (f"🗳️ WHICH STORY MATTERS MORE?\n🅰️ {a['headline']}\n🅱️ {b['headline']}\n💬 Comment A or B 👇\n"
           f"📰 Full stories: {SITE}\n#Poll #News #Reels")
    return {"key": key, "kind": "poll", "reel": reel, "caption": cap, "title": "Reel poll A vs B"}


def desk_candidate(ledger, t):
    day = t.strftime("%Y-%m-%d")
    pool = fresh_pool(t, 14)
    for cat in ("business", "tech", "politics", "world", "sports"):
        key = f"rl:desk:{cat}:{day}"
        items = [a for a in pool if a["category"] == cat][:3]
        if key in ledger or len(items) < 3:
            continue
        stories = [(CAT[cat], a["headline"], a["meta_description"]) for a in items]
        reel = R.news_reel(f"desk_{cat}_{t.strftime('%Y%m%d')}", stories, theme=DESK[cat])
        cap = (f"📌 {DESK[cat][1]} | {CAT[cat].upper()}\n" + "\n".join(f"• {a['headline']}" for a in items)
               + f"\n💬 Which one do you want to know more about? 👇\n📰 {SITE}\n{HASH[cat]} #Reels")
        return {"key": key, "kind": "desk", "reel": reel, "caption": cap, "title": f"Reel {cat} brief"}


def kinds_today(ledger, t):
    day = t.strftime("%Y-%m-%d")
    out = {}
    for k, v in ledger.items():
        if k.startswith("rl:") and str(v.get("ts", "")).startswith(day) and v.get("status") in ("posted", "posting"):
            kind = k.split(":")[1]
            out[kind] = out.get(kind, 0) + 1
    return out


def last_reel_age_min(ledger, t):
    ts = [fb.parse(v["ts"]) for k, v in ledger.items()
          if k.startswith("rl:") and v.get("status") in ("posted", "posting") and v.get("ts")]
    return (t - max(ts)).total_seconds() / 60 if ts else 10 ** 6


def reels_today(ledger, t):
    day = t.strftime("%Y-%m-%d")
    return sum(1 for k, v in ledger.items() if k.startswith("rl:") and v.get("status") in ("posted", "posting")
               and str(v.get("ts", "")).startswith(day))


# --------------------------------------------------------------------------- Meta Reels API
def publish_reel(path, caption) -> str:
    g = fb.GRAPH
    r = requests.post(f"{g}/{fb.PAGE_ID}/video_reels", data={"upload_phase": "start", "access_token": fb.TOKEN}, timeout=60)
    body = r.json()
    if r.status_code != 200 or "video_id" not in body:
        raise RuntimeError(f"start failed {r.status_code}: {str(body.get('error', body))[:200]}")
    vid = body["video_id"]
    data = open(path, "rb").read()
    up = requests.post(body.get("upload_url") or f"https://rupload.facebook.com/video-upload/{g.rsplit('/', 1)[-1]}/{vid}",
                       headers={"Authorization": f"OAuth {fb.TOKEN}", "offset": "0", "file_size": str(len(data))},
                       data=data, timeout=300)
    if up.status_code != 200 or not up.json().get("success"):
        raise RuntimeError(f"upload failed {up.status_code}: {up.text[:200]}")
    fin = requests.post(f"{g}/{fb.PAGE_ID}/video_reels",
                        data={"upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
                              "description": caption, "access_token": fb.TOKEN}, timeout=120)
    fb_body = fin.json()
    if fin.status_code != 200 or not fb_body.get("success"):
        raise RuntimeError(f"finish failed {fin.status_code}: {str(fb_body.get('error', fb_body))[:200]}")
    return vid


# --------------------------------------------------------------------------- main
def run() -> int:
    t = fb.now()
    ledger = fb.load_json(fb.LEDGER, {}) or {}
    live = ENABLED and fb.PAGE_ID and fb.TOKEN and fb.LEDGER.exists()
    if live and reels_today(ledger, t) >= MAX_PER_DAY:
        log.info("Daily reel limit reached (%d).", MAX_PER_DAY)
        return 0

    events = []
    for off in (-1, 0, 1):
        events += S.fetch_day(t + timedelta(days=off))
    events = list({e["id"] + e["league"]: e for e in events}.values())
    log.info("ESPN events loaded: %d", len(events))

    if live and not FORCE and last_reel_age_min(ledger, t) < MIN_GAP_MIN:
        log.info("Last reel was less than %d minutes ago - waiting.", MIN_GAP_MIN)
        return 0

    plain = {"news": news_candidate, "break": breaking_candidate, "top5": top5_candidate,
             "poll": poll_candidate, "desk": desk_candidate}
    if FORCE in plain:
        order = [plain[FORCE]]
    elif FORCE in ("result", "matchday"):
        order = [result_candidate if FORCE == "result" else matchday_candidate]
    else:
        # sports results / match-day first, then the news style used least so far today (styles rotate)
        used = kinds_today(ledger, t)
        styles = sorted(plain, key=lambda k: (used.get(k, 0), list(plain).index(k)))
        order = [result_candidate, matchday_candidate] + [plain[k] for k in styles]
    cand = None
    for f in order:
        if f in (result_candidate, matchday_candidate):
            cand = f(events, ledger, t)
        else:
            cand = f(ledger, t)
        if cand:
            break
    if not cand:
        log.info("No reel due this run.")
        return 0

    log.info("%s reel: %s", "LIVE" if live else "PREVIEW", cand["title"])
    if not live:
        path = R.render(cand["reel"])
        (R.OUT / (path.stem + ".txt")).write_text(cand["caption"], encoding="utf-8")
        log.info("PREVIEW rendered %s\n%s", path.name, cand["caption"])
        return 0

    # park stale reel reservations
    changed = False
    for k, v in ledger.items():
        if k.startswith("rl:") and v.get("status") == "posting" and fb.parse(v["ts"]) < t - timedelta(minutes=45):
            v.update(status="needs_review", ts=fb.iso(t), note="reel outcome unknown")
            changed = True
    if changed:
        ledger = fb.push_ledger(ledger, "fb: park stale reels") or ledger

    path = R.render(cand["reel"])                        # render first: a failed render reserves nothing
    ledger[cand["key"]] = {"status": "posting", "ts": fb.iso(fb.now()), "title": cand["title"]}
    merged = fb.push_ledger(ledger, f"fb: reserve {cand['key']}")
    if merged is None:
        log.error("Could not push reservation; NOT posting")
        return 1
    ledger = merged
    if ledger[cand["key"]].get("status") != "posting":
        return 0
    try:
        vid = publish_reel(path, cand["caption"])
        ledger[cand["key"]].update(status="posted", ts=fb.iso(fb.now()), post_id=vid)
        log.info("POSTED reel %s -> %s", cand["title"], vid)
    except Exception as exc:
        log.error("Reel failed: %s", exc)
        fb.push_ledger(ledger, f"fb: reel attempt {cand['key']}")
        return 1
    if fb.push_ledger(ledger, f"fb: posted {cand['key']}") is None:
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s")
    sys.exit(run())
