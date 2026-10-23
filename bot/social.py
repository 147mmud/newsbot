"""Auto-post fresh stories to Bluesky and Telegram (each only if its secrets exist)."""
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

SITE = os.getenv("SITE_URL", "").rstrip("/")
REC = Path("data/social_posted.json")
MAX_RUN = int(os.getenv("SOCIAL_MAX_PER_RUN", "2"))
MAX_DAY = int(os.getenv("SOCIAL_MAX_PER_DAY", "10"))
BSKY_H, BSKY_P = os.getenv("BSKY_HANDLE", ""), os.getenv("BSKY_APP_PASSWORD", "")
TG_T, TG_C = os.getenv("TG_BOT_TOKEN", ""), os.getenv("TG_CHAT_ID", "")
TAGS = {"politics": "#Politics", "world": "#WorldNews", "business": "#Business", "tech": "#Tech", "sports": "#Sports"}


def pick(arts, rec):
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    used_today = sum(1 for v in rec.values() if v.startswith(today))
    room = min(MAX_RUN, MAX_DAY - used_today)
    out, cats = [], set()
    for a in arts:                                   # newest first
        if len(out) >= max(room, 0):
            break
        if a["id"] in rec or not a.get("index", True):
            continue
        if datetime.fromisoformat(a["published"]) < now - timedelta(hours=8):
            continue
        if a["category"] in cats and len(out) < room:      # spread across sections
            continue
        cats.add(a["category"]); out.append(a)
    return out


def bluesky(a, link):
    s = requests.post("https://bsky.social/xrpc/com.atproto.server.createSession",
                      json={"identifier": BSKY_H, "password": BSKY_P}, timeout=20)
    s.raise_for_status(); s = s.json()
    text = f"{a['headline']}"[:240] + f" {TAGS.get(a['category'], '')}"
    embed = {"$type": "app.bsky.embed.external", "external": {
        "uri": link, "title": a["headline"][:200], "description": a["meta_description"][:300]}}
    img = a.get("image")
    if img:
        try:
            b = requests.get(img, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if b.ok and len(b.content) < 950_000:
                up = requests.post("https://bsky.social/xrpc/com.atproto.repo.uploadBlob", data=b.content,
                                   headers={"Authorization": "Bearer " + s["accessJwt"],
                                            "Content-Type": b.headers.get("content-type", "image/jpeg")}, timeout=30)
                if up.ok:
                    embed["external"]["thumb"] = up.json()["blob"]
        except Exception:
            pass
    r = requests.post("https://bsky.social/xrpc/com.atproto.repo.createRecord", headers={"Authorization": "Bearer " + s["accessJwt"]},
                      json={"repo": s["did"], "collection": "app.bsky.feed.post", "record": {
                          "$type": "app.bsky.feed.post", "text": text, "embed": embed,
                          "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "langs": ["en"]}}, timeout=30)
    r.raise_for_status()


def telegram(a, link):
    msg = f"<b>{a['headline']}</b>\n{a['meta_description'][:250]}\n\n{link}"
    r = requests.post(f"https://api.telegram.org/bot{TG_T}/sendMessage",
                      json={"chat_id": TG_C, "text": msg, "parse_mode": "HTML"}, timeout=20)
    r.raise_for_status()


def main() -> int:
    if not ((BSKY_H and BSKY_P) or (TG_T and TG_C)):
        print("no social secrets set - skipping"); return 0
    arts = json.loads(Path("data/articles.json").read_text())
    arts = arts["articles"] if isinstance(arts, dict) else arts
    arts.sort(key=lambda a: a["published"], reverse=True)
    rec = json.loads(REC.read_text()) if REC.exists() else {}
    for a in pick(arts, rec):
        link = f"{SITE}/news/{a['slug']}/"
        ok = False
        for name, cond, fn in (("bluesky", BSKY_H and BSKY_P, bluesky), ("telegram", TG_T and TG_C, telegram)):
            if cond:
                try:
                    fn(a, link); ok = True; print("posted", name, a["slug"])
                except Exception as exc:
                    print(name, "failed:", exc)
        if ok:
            rec[a["id"]] = datetime.now(timezone.utc).isoformat()
    REC.parent.mkdir(exist_ok=True)
    REC.write_text(json.dumps(rec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
