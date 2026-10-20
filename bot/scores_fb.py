"""Facebook score posts: match-day previews, kick-off alerts, results, Sunday round-up.

    python -m bot.scores_fb          # what the hourly GitHub Actions job runs

* PREVIEW (default): builds the cards + captions into ./previews/ and logs
  them - nothing is posted and the ledger is not touched.
* LIVE: only when FB_SCORES_ENABLED=true and FB_PAGE_ID / FB_PAGE_TOKEN exist.
* Shares data/fb_posted.json with the news poster (keys start with "sc:"),
  using the same reserve -> push -> post -> record flow, so nothing is ever
  posted twice. A reservation left behind by a crash is parked as
  needs_review, never retried blindly.
* Audience: US / UK / Canada / Europe. Only top competitions, times shown in
  UK + US Eastern, posts capped per run and per day.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont

from bot import facebook as fb

log = logging.getLogger("scores_fb")

ROOT = Path(__file__).resolve().parent.parent
PREVIEW_DIR = ROOT / "previews"
LOGO = ROOT / "static" / "img" / "fb_logo.png"
SITE = os.getenv("SITE_URL", "https://thepressandscore.com").rstrip("/")

ENABLED = os.getenv("FB_SCORES_ENABLED", "").strip().lower() == "true"
MAX_PER_RUN = int(os.getenv("FB_SCORES_MAX_PER_RUN", "2"))
MAX_PER_DAY = int(os.getenv("FB_SCORES_MAX_PER_DAY", "6"))
UK, ET = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
ESPN = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard?dates={day}"
UA = {"User-Agent": "Mozilla/5.0 (compatible; PressAndScoreBot/1.0; +https://thepressandscore.com)"}

# --------------------------------------------------------------------------- competitions
# (key, ESPN path, label, sport, priority, emoji, hashtag)
LEAGUES = [
    ("ucl",  "soccer/uefa.champions", "CHAMPIONS LEAGUE", "football", 10, "⚽", "#ChampionsLeague"),
    ("epl",  "soccer/eng.1",          "PREMIER LEAGUE",   "football", 9,  "⚽", "#PremierLeague"),
    ("nfl",  "football/nfl",          "NFL",              "nfl",      9,  "🏈", "#NFL"),
    ("six",  "rugby/180659",          "SIX NATIONS",      "rugby",    9,  "🏉", "#SixNations"),
    ("prem", "rugby/267979",          "PREM RUGBY",       "rugby",    7,  "🏉", "#PremRugby"),
    ("urc",  "rugby/270557",          "URC",              "rugby",    6,  "🏉", "#URC"),
]
# clubs with big US/UK followings: their games get kick-off alerts
BIG = {
    "epl": {"ARS", "MNC", "MAN", "LIV", "CHE", "TOT", "NEW", "AVL"},
    "ucl": {"ARS", "MNC", "MAN", "LIV", "CHE", "TOT", "NEW", "AVL", "RMA", "BAR", "BAY", "PSG", "JUV", "INT", "MIL"},
    "nfl": None,   # every NFL game
    "six": None,   # every Six Nations game
    "prem": {"BAT", "GLO", "LEI", "NOR", "SAR", "BRI", "HAR", "EXE", "SAL", "BAT"},
    "urc": {"LEI", "ULS", "MUN", "GLA", "EDI", "CAR", "OSP", "SCA", "DRA", "BUL", "STO", "SHA"},
}
MARGIN = {"football": (1, 3), "nfl": (3, 21), "rugby": (3, 21)}      # (thriller <=, blowout >=)
HIGH_SCORE = {"football": 5, "nfl": 60, "rugby": 65}


# --------------------------------------------------------------------------- data
def fetch_day(day: datetime) -> list[dict]:
    out = []
    for key, path, label, sport, prio, emo, tag in LEAGUES:
        try:
            r = requests.get(ESPN.format(path=path, day=day.strftime("%Y%m%d")), headers=UA, timeout=15)
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("ESPN %s failed: %s", key, exc)
            continue
        for ev in data.get("events", []) or []:
            comp = (ev.get("competitions") or [{}])[0]
            teams = []
            for c in comp.get("competitors", []) or []:
                t = c.get("team") or {}
                rec = (c.get("records") or [{}])[0].get("summary") or c.get("form") or ""
                teams.append({
                    "name": t.get("shortDisplayName") or t.get("displayName") or "",
                    "abbr": t.get("abbreviation") or "",
                    "color": "#" + (t.get("color") or "13294b")[:6],
                    "score": c.get("score") if isinstance(c.get("score"), str) else str(c.get("score") or ""),
                    "home": c.get("homeAway") == "home",
                    "winner": bool(c.get("winner")),
                    "record": rec,
                })
            if len(teams) != 2:
                continue
            teams.sort(key=lambda t: not t["home"])          # home team first (UK style)
            st = (ev.get("status") or {}).get("type") or {}
            out.append({
                "id": str(ev.get("id")), "league": key, "label": label, "sport": sport, "prio": prio,
                "emoji": emo, "tag": tag, "date": ev.get("date", ""), "state": st.get("state", "pre"),
                "completed": bool(st.get("completed")), "detail": st.get("shortDetail", ""),
                "venue": (comp.get("venue") or {}).get("fullName", ""), "teams": teams,
            })
    return out


def kickoff(e: dict) -> datetime:
    return datetime.fromisoformat(e["date"].replace("Z", "+00:00"))


def times(e: dict) -> tuple[str, str]:
    k = kickoff(e)
    f = lambda d: d.strftime("%-I:%M %p")
    return f(k.astimezone(UK)) + " UK", f(k.astimezone(ET)) + " ET"


def is_big(e: dict) -> bool:
    teams = BIG.get(e["league"])
    return teams is None or any(t["abbr"] in teams for t in e["teams"])


def win_pct(rec: str) -> tuple[float, int]:
    if re.fullmatch(r"[WLDT]+", rec or ""):                   # form string, e.g. WWLWL
        g = len(rec)
        return ((rec.count("W") + 0.5 * (rec.count("D") + rec.count("T"))) / g if g else 0.5), g
    nums = [int(x) for x in re.findall(r"\d+", rec or "")]
    if len(nums) == 3:                                        # soccer W-D-L
        w, d, l = nums
    elif len(nums) == 2:
        (w, l), d = nums, 0
    else:
        return 0.5, 0
    g = w + d + l
    return ((w + 0.5 * d) / g if g else 0.5), g


def verdict(e: dict) -> dict:
    """Classify a finished match -> label, line, excitement score."""
    a, b = e["teams"]
    try:
        sa, sb = int(float(a["score"])), int(float(b["score"]))
    except ValueError:
        return {"label": None, "line": "", "excite": 0}
    thr, blow = MARGIN[e["sport"]]
    margin, total = abs(sa - sb), sa + sb
    win, lose = (a, b) if sa > sb else (b, a)
    pw, gw = win_pct(win["record"])
    pl, gl = win_pct(lose["record"])
    if sa == sb:
        return {"label": "HONOURS EVEN", "emoji": "🤝", "line": "Nothing to separate them", "excite": 2}
    if gw >= 3 and gl >= 3 and pl - pw >= 0.25:
        return {"label": "UPSET ALERT", "emoji": "🚨", "line": f"{win['name']} stun {lose['name']}", "excite": 5}
    if margin <= thr and (e["sport"] != "football" or total >= 3):
        pts = "goal" if e["sport"] == "football" else "point"
        return {"label": "NAIL-BITER", "emoji": "😱",
                "line": f"Decided by {margin} {pts}{'s' if margin > 1 else ''}", "excite": 4}
    if total >= HIGH_SCORE[e["sport"]]:
        return {"label": "SHOOTOUT" if e["sport"] != "football" else "GOAL FEST", "emoji": "💥",
                "line": f"{total} {'goals' if e['sport'] == 'football' else 'points'} on the board", "excite": 3}
    if margin >= blow:
        return {"label": "STATEMENT WIN", "emoji": "🔥", "line": f"A {margin}-{'goal' if e['sport']=='football' else 'point'} demolition", "excite": 3}
    return {"label": None, "line": f"{win['name']} take the win", "excite": 1}


# --------------------------------------------------------------------------- images
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, serif: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSerif-Bold.ttf" if serif else "DejaVuSans-Bold.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size=size)


def hexrgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        r, g, b = 19, 41, 75
    # keep colours deep enough for white text
    k = min(1.0, 140 / max(1, (r * 299 + g * 587 + b * 114) / 1000))
    return int(r * k), int(g * k), int(b * k)


def fit_text(d: ImageDraw.ImageDraw, text: str, max_w: int, size: int, serif=False) -> ImageFont.FreeTypeFont:
    while size > 18:
        f = font(size, serif)
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 2
    return font(size, serif)


def center(d, y, text, f, fill="white", x0=0, x1=1080):
    w = d.textlength(text, font=f)
    d.text((x0 + (x1 - x0 - w) / 2, y), text, font=f, fill=fill)


def base(c1="#0b1d33", c2="#1b3a66", split=True) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (1080, 1080), hexrgb(c1))
    d = ImageDraw.Draw(img)
    if split:
        d.polygon([(620, 0), (1080, 0), (1080, 1080), (380, 1080)], fill=hexrgb(c2))
    else:
        for y in range(1080):                                   # vertical gradient
            t = y / 1080
            a, b = hexrgb(c1), hexrgb(c2)
            d.line([(0, y), (1080, y)], fill=tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    shade = Image.new("L", (1080, 1080), 0)
    ImageDraw.Draw(shade).ellipse((-300, -200, 1380, 1300), fill=0)
    img = Image.composite(img, Image.new("RGB", img.size, (0, 0, 0)), Image.eval(shade, lambda v: 255 - 70))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 1080, 110), fill=(11, 29, 51))
    if LOGO.exists():
        lg = Image.open(LOGO).convert("RGB").resize((64, 64))
        m = Image.new("L", (64, 64), 0)
        ImageDraw.Draw(m).ellipse((0, 0, 63, 63), fill=255)
        img.paste(lg, (48, 23), m)
    d.text((128, 34), "The Press & Score", font=font(32, serif=True), fill="white")
    return img, d


def chip(d, text, y, fill=(255, 255, 255), fg=(11, 29, 51), x_center=540, size=26):
    f = font(size)
    w = d.textlength(text, font=f)
    d.rounded_rectangle((x_center - w / 2 - 24, y, x_center + w / 2 + 24, y + size + 26), radius=40, fill=fill)
    d.text((x_center - w / 2, y + 12), text, font=f, fill=fg)


def banner(img, text, y, fill):
    """Tilted label like a sticker."""
    f = font(58)
    tmp = Image.new("RGBA", (1080, 140), (0, 0, 0, 0))
    td = ImageDraw.Draw(tmp)
    w = td.textlength(text, font=f)
    td.rectangle((540 - w / 2 - 40, 25, 540 + w / 2 + 40, 115), fill=fill)
    td.text((540 - w / 2, 34), text, font=f, fill="white" if fill != (245, 158, 11) else (17, 17, 17))
    tmp = tmp.rotate(3, resample=Image.BICUBIC)
    img.paste(tmp, (0, y), tmp)


def footer(d, text):
    d.rectangle((0, 960, 1080, 1080), fill=(200, 16, 46))
    center(d, 995, text, fit_text(d, text, 1000, 40))


def card_final(e: dict, v: dict) -> Image.Image:
    a, b = e["teams"]
    img, d = base(a["color"], b["color"])
    if v.get("label"):
        colors = {"UPSET ALERT": (124, 58, 237), "NAIL-BITER": (245, 158, 11), "HONOURS EVEN": (15, 118, 110)}
        banner(img, v["label"], 160, colors.get(v["label"], (200, 16, 46)))
        d = ImageDraw.Draw(img)
    else:
        chip(d, e["label"], 175)
    for x0, t in ((0, a), (560, b)):
        center(d, 340, t["name"].upper(), fit_text(d, t["name"].upper(), 460, 66), x0=x0, x1=x0 + 520)
        center(d, 420, t["score"], font(230), x0=x0, x1=x0 + 520)
    center(d, 455 + 60, "-", font(80))
    center(d, 735, "FULL TIME" + (f"  ·  {e['label']}" if v.get("label") else ""), font(32))
    if v.get("line"):
        center(d, 795, v["line"], fit_text(d, v["line"], 960, 34))
    footer(d, "THEPRESSANDSCORE.COM")
    return img


def card_prematch(e: dict) -> Image.Image:
    a, b = e["teams"]
    img, d = base(a["color"], b["color"])
    chip(d, e["label"], 150)
    banner(img, "MATCH DAY", 225, (245, 158, 11))
    d = ImageDraw.Draw(img)
    for x0, t in ((0, a), (580, b)):
        center(d, 440, t["name"].upper(), fit_text(d, t["name"].upper(), 440, 80), x0=x0, x1=x0 + 500)
    center(d, 450, "VS", font(56), fill=(245, 158, 11))
    uk, et = times(e)
    center(d, 610, f"KICK-OFF  {uk}  ·  {et}", fit_text(d, f"KICK-OFF  {uk}  ·  {et}", 1000, 40))
    if e["venue"]:
        center(d, 680, e["venue"], fit_text(d, e["venue"], 960, 30), fill=(220, 225, 235))
    footer(d, "YOUR PREDICTION? COMMENT BELOW")
    return img


def card_list(title, kicker, rows, foot, c1="#0b1d33", c2="#1b3a66") -> Image.Image:
    img, d = base(c1, c2, split=False)
    d.text((60, 150), kicker, font=font(28), fill=(252, 165, 165))
    d.text((60, 190), title, font=fit_text(d, title, 960, 84), fill="white")
    y = 320
    for top, main, right, sub in rows[:5]:
        d.rounded_rectangle((60, y, 1020, y + 112), radius=18, fill=(30, 50, 82))
        d.text((90, y + 16), top, font=font(22), fill=(167, 139, 250))
        d.text((90, y + 50), main, font=fit_text(d, main, 640, 38), fill="white")
        fr = font(30)
        d.text((990 - d.textlength(right, font=fr), y + 22), right, font=fr, fill="white")
        if sub:
            fs = font(20)
            d.text((990 - d.textlength(sub, font=fs), y + 66), sub, font=fs, fill=(200, 205, 215))
        y += 128
    footer(d, foot)
    return img


# --------------------------------------------------------------------------- captions
def tags(*events) -> str:
    seen = []
    for e in events:
        if e["tag"] not in seen:
            seen.append(e["tag"])
    return " ".join(seen[:3])


def cap_final(e, v) -> str:
    a, b = e["teams"]
    score = f"{a['name']} {a['score']}–{b['score']} {b['name']}"
    head = {
        "NAIL-BITER": "😱 WHAT. A. FINISH.",
        "UPSET ALERT": "🚨 UPSET ALERT!",
        "STATEMENT WIN": "🔥 STATEMENT WIN.",
        "SHOOTOUT": "💥 WHAT A SHOOTOUT!",
        "GOAL FEST": "💥 GOAL FEST!",
        "HONOURS EVEN": "🤝 HONOURS EVEN.",
    }.get(v.get("label"), f"{e['emoji']} FULL TIME | {e['label'].title()}")
    q = {"UPSET ALERT": "Did you see this coming? 👇",
         "NAIL-BITER": "Who was your player of the match? 👇",
         "HONOURS EVEN": "Fair result? Tell us below 👇"}.get(v.get("label"), "Rate this game out of 10 👇")
    line = f" {v['line']}." if v.get("line") else ""
    return (f"{head}\n{e['emoji']} {e['label'].title()}: {score}.{line}\n💬 {q}\n"
            f"📊 Every score, live: {SITE.replace('https://', '')}\n{tags(e)}")


def cap_prematch(e) -> str:
    a, b = e["teams"]
    uk, et = times(e)
    venue = f" at {e['venue']}" if e["venue"] else ""
    return (f"⏰ MATCH DAY | {e['label'].title()}\n{e['emoji']} {a['name']} vs {b['name']}{venue}\n"
            f"🕒 Kick-off {uk} / {et}\n🔮 Drop your score prediction below 👇\n"
            f"📊 Follow it live: {SITE.replace('https://', '')}\n{tags(e)}")


# --------------------------------------------------------------------------- planning
def plan(events: list[dict], ledger: dict, t: datetime) -> list[dict]:
    """Return candidate posts in priority order: {key, kind, image, caption, events}."""
    posts = []
    # 1) results: finished within the last 8h
    for e in events:
        if e["state"] == "post" and e["completed"] and kickoff(e) > t - timedelta(hours=11):
            key = f"sc:final:{e['id']}"
            if key in ledger:
                continue
            v = verdict(e)
            if not v.get("label") and not is_big(e):
                continue                                   # ordinary small game: skip
            posts.append({"key": key, "kind": "final", "rank": 100 + e["prio"] * 2 + v["excite"] * 3,
                          "make": lambda e=e, v=v: (card_final(e, v), cap_final(e, v)), "title": f"FT {e['teams'][0]['name']} v {e['teams'][1]['name']}"})
    # 2) kick-off alerts: big games starting in 15 - 150 minutes
    for e in events:
        k = kickoff(e)
        if e["state"] == "pre" and t + timedelta(minutes=15) <= k <= t + timedelta(minutes=150) and is_big(e):
            key = f"sc:pre:{e['id']}"
            if key not in ledger:
                posts.append({"key": key, "kind": "pre", "rank": 80 + e["prio"],
                              "make": lambda e=e: (card_prematch(e), cap_prematch(e)), "title": f"KO {e['teams'][0]['name']} v {e['teams'][1]['name']}"})
    # 3) morning "big games today" (from 10:00 UTC = 11am UK / 6am ET)
    day = t.strftime("%Y-%m-%d")
    key = f"sc:today:{day}"
    if t.hour >= 10 and key not in ledger:
        today = [e for e in events if e["state"] == "pre" and t <= kickoff(e) <= t + timedelta(hours=16) and is_big(e)]
        today.sort(key=lambda e: (-e["prio"], kickoff(e)))
        if len(today) >= 2:
            top = sorted(today[:5], key=kickoff)
            rows = [(e["label"], f"{e['teams'][0]['name']} vs {e['teams'][1]['name']}", *times(e)) for e in top]
            cap = (f"📅 BIG GAMES TODAY\n{len(top)} huge fixtures for your {t.astimezone(ET).strftime('%A')}. Which one are you watching? 🍿\n"
                   + "\n".join(f"{e['emoji']} {e['teams'][0]['name']} vs {e['teams'][1]['name']} ({times(e)[0]} / {times(e)[1]})" for e in top)
                   + f"\n🔮 Pick ONE game and predict the score below 👇\n📊 Live scores all day: {SITE.replace('https://', '')}\n{tags(*top)}")
            posts.append({"key": key, "kind": "today", "rank": 95,
                          "make": lambda rows=rows, cap=cap, d=t: (card_list("BIG GAMES TODAY", d.astimezone(UK).strftime("%A").upper(), rows,
                                                                       "PREDICT ONE SCORE BELOW"), cap), "title": "Big games today"})
    # 4) Sunday round-up after 19:00 UTC
    key = f"sc:week:{day}"
    if t.weekday() == 6 and t.hour >= 19 and key not in ledger:
        done = [e for e in events if e["state"] == "post" and e["completed"] and kickoff(e) > t - timedelta(days=3)]
        scored = sorted(((verdict(e), e) for e in done), key=lambda ve: (-ve[0]["excite"], -ve[1]["prio"]))[:4]
        if len(scored) >= 3:
            rows = [(e["label"], f"{e['teams'][0]['name']} {e['teams'][0]['score']}–{e['teams'][1]['score']} {e['teams'][1]['name']}",
                     {"UPSET ALERT": "Upset", "NAIL-BITER": "Nail-biter", "STATEMENT WIN": "Rout", "SHOOTOUT": "Shootout",
                      "GOAL FEST": "Goal fest", "HONOURS EVEN": "Draw"}.get(v.get("label"), "Full time"), "") for v, e in scored]
            cap = ("🗓️ YOUR WEEKEND IN " + str(len(rows)) + " SCORES\n"
                   + "\n".join(f"{e['emoji']} {r[1]}" for (v, e), r in zip(scored, rows))
                   + f"\n🤯 Which result shocked you the most? 👇\n📊 Every result: {SITE.replace('https://', '')}\n{tags(*[e for _, e in scored])}")
            posts.append({"key": key, "kind": "week", "rank": 90,
                          "make": lambda rows=rows, cap=cap: (card_list(f"WEEKEND IN {len(rows)} SCORES", "SUNDAY ROUND-UP", rows,
                                                                       "WHICH RESULT SHOCKED YOU?", "#2a0a14", "#0b1d33"), cap), "title": "Weekend round-up"})
    posts.sort(key=lambda p: -p["rank"])
    return posts


def posted_today(ledger: dict, t: datetime) -> int:
    day = t.strftime("%Y-%m-%d")
    return sum(1 for k, v in ledger.items() if k.startswith("sc:") and v.get("status") in ("posted", "posting")
               and str(v.get("ts", "")).startswith(day))


# --------------------------------------------------------------------------- Facebook
def post_photo(img: Image.Image, caption: str) -> dict:
    import io
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90)
    r = requests.post(f"{fb.GRAPH}/{fb.PAGE_ID}/photos",
                      data={"caption": caption, "access_token": fb.TOKEN},
                      files={"source": ("card.jpg", buf.getvalue(), "image/jpeg")}, timeout=60)
    body = r.json() if r.content else {}
    if r.status_code != 200 or "error" in body:
        err = body.get("error", {})
        raise RuntimeError(f"Graph API {r.status_code} code={err.get('code')} {err.get('message', '')[:200]}")
    return body


# --------------------------------------------------------------------------- main
def run() -> int:
    t = fb.now()
    events = []
    for off in (-1, 0, 1):                                   # yesterday/today/tomorrow (US evening spans UTC midnight)
        events += fetch_day(t + timedelta(days=off))
    uniq = {e["id"] + e["league"]: e for e in events}
    events = list(uniq.values())
    log.info("ESPN events loaded: %d", len(events))

    ledger = fb.load_json(fb.LEDGER, {}) or {}
    live = ENABLED and fb.PAGE_ID and fb.TOKEN and fb.LEDGER.exists()
    candidates = plan(events, ledger, t)
    room = max(0, MAX_PER_DAY - posted_today(ledger, t))
    batch = candidates[: min(MAX_PER_RUN, room)]
    log.info("%s: %d candidate posts, posting up to %d (daily room %d)",
             "LIVE" if live else "PREVIEW", len(candidates), len(batch), room)

    if not live:
        PREVIEW_DIR.mkdir(exist_ok=True)
        for i, p in enumerate(candidates[:8]):
            img, cap = p["make"]()
            name = f"{i + 1:02d}_{p['kind']}.jpg"
            img.save(PREVIEW_DIR / name, "JPEG", quality=88)
            (PREVIEW_DIR / name.replace(".jpg", ".txt")).write_text(cap, encoding="utf-8")
            log.info("PREVIEW %s %s\n%s", name, "(would post)" if p in batch else "(over limit)", cap)
        if not candidates:
            log.info("No score posts due right now.")
        return 0

    # park stale score reservations for a human (never re-post blindly)
    changed = False
    for k, v in ledger.items():
        if k.startswith("sc:") and v.get("status") == "posting" and fb.parse(v["ts"]) < t - timedelta(minutes=fb.STALE_MIN):
            v.update(status="needs_review", ts=fb.iso(t), note="post outcome unknown")
            changed = True
    if changed:
        ledger = fb.push_ledger(ledger, "fb: park stale score posts") or ledger

    for p in batch:
        img, cap = p["make"]()
        ledger[p["key"]] = {"status": "posting", "ts": fb.iso(fb.now()), "title": p["title"]}
        merged = fb.push_ledger(ledger, f"fb: reserve {p['key']}")
        if merged is None:
            log.error("Could not push reservation; NOT posting %s", p["key"])
            return 1
        ledger = merged
        if ledger[p["key"]].get("status") != "posting":
            continue
        try:
            res = post_photo(img, cap)
            ledger[p["key"]].update(status="posted", ts=fb.iso(fb.now()), post_id=res.get("post_id") or res.get("id"))
            log.info("POSTED %s -> %s", p["title"], ledger[p["key"]]["post_id"])
        except Exception as exc:
            log.error("Post failed for %s: %s", p["key"], exc)
            fb.push_ledger(ledger, f"fb: score post attempt {p['key']}")
            return 1
        if fb.push_ledger(ledger, f"fb: posted {p['key']}") is None:
            log.error("Posted but could not record %s", p["key"])
            return 1
    if not batch:
        log.info("No score post due this run.")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)-9s %(message)s")
    sys.exit(run())
