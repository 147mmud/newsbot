"""Server-rendered score hub pages (/scores/ and /scores/<league>/) for search traffic.
Pure requests (no Pillow) so it runs inside the normal site build."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger("scoreshub")
UK, ET = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")
HOSTS = ["https://site.api.espn.com", "https://site.web.api.espn.com"]
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
      "Accept": "application/json"}

# slug, ESPN path, display name, sport word, blurb
LEAGUES = [
    ("nfl", "football/nfl", "NFL", "American football", "NFL scores, results and upcoming games, updated through every game day."),
    ("premier-league", "soccer/eng.1", "Premier League", "football", "Premier League scores, results and fixtures from England's top flight."),
    ("champions-league", "soccer/uefa.champions", "Champions League", "football", "UEFA Champions League scores, results and fixtures."),
    ("la-liga", "soccer/esp.1", "La Liga", "football", "La Liga scores, results and fixtures from Spain."),
    ("bundesliga", "soccer/ger.1", "Bundesliga", "football", "Bundesliga scores, results and fixtures from Germany."),
    ("serie-a", "soccer/ita.1", "Serie A", "football", "Serie A scores, results and fixtures from Italy."),
    ("mls", "soccer/usa.1", "MLS", "football", "MLS scores, results and fixtures."),
    ("nba", "basketball/nba", "NBA", "basketball", "NBA scores, results and upcoming games."),
    ("mlb", "baseball/mlb", "MLB", "baseball", "MLB scores, results and upcoming games."),
    ("nhl", "hockey/nhl", "NHL", "ice hockey", "NHL scores, results and upcoming games."),
    ("six-nations", "rugby/180659", "Six Nations", "rugby union", "Six Nations rugby scores, results and fixtures."),
    ("premiership-rugby", "rugby/267979", "Premiership Rugby", "rugby union", "Premiership Rugby scores, results and fixtures."),
    ("urc", "rugby/270557", "United Rugby Championship", "rugby union", "URC rugby scores, results and fixtures."),
]


def _get(path: str, day: datetime):
    last = None
    for host in HOSTS:
        try:
            r = requests.get(f"{host}/apis/site/v2/sports/{path}/scoreboard?dates={day:%Y%m%d}", headers=UA, timeout=15)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            last = exc
    raise last


def _fmt(d: datetime, tz) -> str:
    return d.astimezone(tz).strftime("%a %-d %b, %-I:%M %p")


def league_games(path: str) -> list[dict]:
    now = datetime.now(timezone.utc)
    seen, out = set(), []
    for off in (-1, 0, 1, 2):
        try:
            data = _get(path, now + timedelta(days=off))
        except Exception as exc:
            log.warning("scores %s day %+d failed: %s", path, off, exc)
            continue
        for ev in data.get("events", []) or []:
            if ev.get("id") in seen:
                continue
            seen.add(ev.get("id"))
            comp = (ev.get("competitions") or [{}])[0]
            teams = []
            for c in comp.get("competitors", []) or []:
                t = c.get("team") or {}
                teams.append({"name": t.get("displayName") or t.get("name") or "", "logo": t.get("logo", ""),
                              "score": str(c.get("score") if not isinstance(c.get("score"), dict) else c["score"].get("displayValue") or ""),
                              "home": c.get("homeAway") == "home", "winner": bool(c.get("winner"))})
            if len(teams) != 2:
                continue
            teams.sort(key=lambda t: not t["home"])
            st = (ev.get("status") or {}).get("type") or {}
            try:
                ko = datetime.fromisoformat(ev["date"].replace("Z", "+00:00"))
            except Exception:
                continue
            out.append({"id": ev.get("id"), "state": st.get("state", "pre"), "detail": st.get("shortDetail", ""),
                        "ko": ko, "uk": _fmt(ko, UK) + " UK", "et": _fmt(ko, ET) + " ET",
                        "venue": (comp.get("venue") or {}).get("fullName", ""), "teams": teams})
    out.sort(key=lambda g: g["ko"])
    return out


def fetch_all() -> list[dict]:
    res = []
    for slug, path, name, sport, blurb in LEAGUES:
        games = league_games(path)
        res.append({"slug": slug, "name": name, "sport": sport, "blurb": blurb, "games": games,
                    "live": [g for g in games if g["state"] == "in"],
                    "results": [g for g in games if g["state"] == "post"][::-1],
                    "upcoming": [g for g in games if g["state"] == "pre"]})
        log.info("hub %s: %d games", slug, len(games))
    return res
