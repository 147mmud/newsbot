"""Send a daily email digest through Brevo (needs BREVO_API_KEY). Once per UTC day, after SEND_HOUR_UTC."""
import html, json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import requests

API = "https://api.brevo.com/v3"
KEY = os.getenv("BREVO_API_KEY", "")
SITE = os.getenv("SITE_URL", "https://thepressandscore.com").rstrip("/")
LIST_NAME = os.getenv("BREVO_LIST_NAME", "Daily News")
SENDER_NAME = os.getenv("BREVO_SENDER_NAME", "The Press & Score")
SENDER_EMAIL = os.getenv("BREVO_SENDER_EMAIL", "")
SEND_HOUR = int(os.getenv("SEND_HOUR_UTC", "12"))
REC = Path("data/newsletter.json")
H = lambda: {"api-key": KEY, "accept": "application/json", "content-type": "application/json"}


def top_stories(arts, n=6):
    cut = datetime.now(timezone.utc) - timedelta(hours=26)
    fresh = [a for a in arts if a.get("index", True) and datetime.fromisoformat(a["published"]) > cut]
    fresh.sort(key=lambda a: a["published"], reverse=True)
    out, seen = [], set()
    for a in fresh:                       # one per category first, then fill
        if a["category"] not in seen:
            seen.add(a["category"]); out.append(a)
    for a in fresh:
        if len(out) >= n: break
        if a not in out: out.append(a)
    return out[:n]


def render(stories, today):
    rows = ""
    for a in stories:
        link = f"{SITE}/news/{a['slug']}/"
        img = (f'<img src="{html.escape(a["image"])}" width="520" style="width:100%;max-width:520px;border-radius:6px;display:block;margin-bottom:8px" alt="">'
               if a.get("image") else "")
        rows += (f'<tr><td style="padding:16px 0;border-bottom:1px solid #e5e7eb">{img}'
                 f'<div style="font:600 11px Arial;color:#1d4ed8;text-transform:uppercase;letter-spacing:.5px">{html.escape(a["category"])}</div>'
                 f'<a href="{link}" style="font:700 19px Georgia,serif;color:#0f172a;text-decoration:none">{html.escape(a["headline"])}</a>'
                 f'<p style="font:15px/1.5 Arial;color:#475569;margin:6px 0 0">{html.escape(a["meta_description"][:220])}</p></td></tr>')
    return (f'<div style="background:#f1f5f9;padding:20px 0"><table width="560" align="center" style="background:#fff;padding:20px;border-radius:8px;font-family:Arial" cellpadding="0">'
            f'<tr><td style="text-align:center;padding-bottom:10px"><div style="font:700 24px Georgia,serif;color:#0f172a">The Press &amp; Score</div>'
            f'<div style="font:13px Arial;color:#64748b">Your daily briefing · {today}</div></td></tr>{rows}'
            f'<tr><td style="text-align:center;padding:18px 0"><a href="{SITE}/scores/" style="background:#0f172a;color:#fff;padding:10px 18px;border-radius:6px;font:600 14px Arial;text-decoration:none">Live scores &amp; results</a></td></tr>'
            f'<tr><td style="text-align:center;font:12px Arial;color:#94a3b8">You get this because you subscribed at {SITE.split("//")[-1]}.<br>'
            f'<a href="{{{{ unsubscribe }}}}" style="color:#94a3b8">Unsubscribe</a></td></tr></table></div>')


def list_id():
    r = requests.get(f"{API}/contacts/lists?limit=50", headers=H(), timeout=20); r.raise_for_status()
    for l in r.json().get("lists", []):
        if l["name"].lower() == LIST_NAME.lower():
            return l["id"], l.get("uniqueSubscribers", 0)
    raise SystemExit(f"list {LIST_NAME!r} not found")


def main(test_to: str = "") -> int:
    if not KEY or not SENDER_EMAIL:
        print("BREVO_API_KEY / BREVO_SENDER_EMAIL not set - skipping"); return 0
    now = datetime.now(timezone.utc); today = now.strftime("%Y-%m-%d")
    rec = json.loads(REC.read_text()) if REC.exists() else {}
    if not test_to and (rec.get("last") == today or now.hour < SEND_HOUR):
        print("not due"); return 0
    arts = json.loads(Path("data/articles.json").read_text())
    arts = arts["articles"] if isinstance(arts, dict) else arts
    stories = top_stories(arts)
    if len(stories) < 3:
        print("too few fresh stories"); return 0
    lid, subs = list_id()
    if subs == 0 and not test_to:
        print("no subscribers yet"); return 0
    body = {"name": f"Daily {today}{' TEST' if test_to else ''}", "subject": f"{stories[0]['headline'][:80]}",
            "sender": {"name": SENDER_NAME, "email": SENDER_EMAIL},
            "htmlContent": render(stories, now.strftime("%A, %B %d").replace(" 0", " ")),
            "recipients": {"listIds": [lid]}}
    r = requests.post(f"{API}/emailCampaigns", headers=H(), json=body, timeout=30)
    if not r.ok: print("create failed", r.status_code, r.text[:300]); return 1
    cid = r.json()["id"]
    if test_to:
        s = requests.post(f"{API}/emailCampaigns/{cid}/sendTest", headers=H(), json={"emailTo": [test_to]}, timeout=30)
    else:
        s = requests.post(f"{API}/emailCampaigns/{cid}/sendNow", headers=H(), timeout=30)
    print("send", s.status_code, s.text[:200])
    if s.ok and not test_to:
        REC.parent.mkdir(exist_ok=True); REC.write_text(json.dumps({"last": today, "campaign": cid}))
    return 0 if s.ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[2] if len(sys.argv) > 2 and sys.argv[1] == "--test" else ""))
