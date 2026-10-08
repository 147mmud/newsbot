"""
Server-side snapshot of live scores (ESPN public JSON, no key).
Written to public/data/scores.json and used by the header widget as a
same-origin fallback if the browser cannot reach ESPN directly.
The normaliser mirrors normalize() in static/js/scores.js.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

import config

log = logging.getLogger("scores")


def _side(c: dict) -> dict:
    team = c.get("team") or {}
    score = c.get("score")
    if isinstance(score, dict):
        score = score.get("displayValue")
    return {
        "name": c.get("displayName") or team.get("displayName") or team.get("name") or "",
        "abbr": c.get("abbreviation") or team.get("abbreviation") or "",
        "score": "" if score is None else str(score),
        "logo": c.get("logo") or team.get("logo") or "",
        "home": c.get("homeAway") == "home",
    }


def normalize(data: dict) -> list[dict]:
    out = []
    # Shape A: /personalized/v2/scoreboard/header
    for sport in data.get("sports", []) or []:
        for league in sport.get("leagues", []) or []:
            lname = league.get("shortName") or league.get("abbreviation") or league.get("name", "")
            for ev in league.get("events", []) or []:
                comps = [_side(c) for c in ev.get("competitors", []) or []]
                if len(comps) < 2:
                    continue
                st = ev.get("fullStatus", {}).get("type", {}) if isinstance(ev.get("fullStatus"), dict) else {}
                out.append({
                    "league": lname,
                    "state": ev.get("status") if isinstance(ev.get("status"), str) else st.get("state", "pre"),
                    "detail": ev.get("summary") or st.get("shortDetail") or "",
                    "date": ev.get("date", ""),
                    "link": ev.get("link", ""),
                    "teams": comps[:2],
                })
    # Shape B: /site/v2/sports/{sport}/{league}/scoreboard
    lname = (data.get("leagues") or [{}])[0].get("abbreviation", "")
    for ev in data.get("events", []) or []:
        comp = (ev.get("competitions") or [{}])[0]
        comps = [_side(c) for c in comp.get("competitors", []) or []]
        if len(comps) < 2:
            continue
        st = (ev.get("status") or {}).get("type", {})
        links = ev.get("links") or []
        out.append({
            "league": lname,
            "state": st.get("state", "pre"),
            "detail": st.get("shortDetail", ""),
            "date": ev.get("date", ""),
            "link": links[0].get("href", "") if links else "",
            "teams": comps[:2],
        })
    order = {"in": 0, "pre": 1, "post": 2}
    out.sort(key=lambda e: (order.get(e["state"], 3), e["date"] if e["state"] != "post" else ""))
    return out[:25]


def snapshot(out_file: Path) -> None:
    result = {"updated": datetime.now(timezone.utc).isoformat(), "sports": {}}
    for sport, url in config.SCORE_SPORTS.items():
        try:
            r = requests.get(url, timeout=config.REQUEST_TIMEOUT,
                             headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            result["sports"][sport] = normalize(r.json())
        except Exception as exc:
            log.warning("Scores %s failed: %s", sport, exc)
            result["sports"][sport] = []
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(result, separators=(",", ":")), encoding="utf-8")
    log.info("Scores snapshot: %s", {k: len(v) for k, v in result["sports"].items()})
