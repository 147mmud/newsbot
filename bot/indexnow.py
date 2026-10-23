"""Tell Bing/Yandex/Seznam/Naver (IndexNow) about new URLs right after each deploy."""
import glob, json, os, re, sys, time
from pathlib import Path
import requests

SITE = os.getenv("SITE_URL", "").rstrip("/")
REC = Path("data/indexnow.json")


def main() -> int:
    keys = [Path(p).stem for p in glob.glob("static/*.txt") if re.fullmatch(r"[0-9a-f]{32}", Path(p).stem)]
    if not SITE or not keys:
        print("no SITE_URL/key"); return 0
    key = keys[0]
    xml = requests.get(SITE + "/sitemap.xml", timeout=30).text
    urls = re.findall(r"<loc>([^<]+)</loc>", xml)
    done = set(json.loads(REC.read_text())) if REC.exists() else set()
    new = [u for u in urls if u not in done][:9000]
    # news pages change often (home, sections, scores): always re-ping the hubs
    hubs = [u for u in urls if u.rstrip("/") == SITE or "/category/" in u or "/scores" in u]
    batch = list(dict.fromkeys(new + hubs))[:9000]
    if not batch:
        print("nothing to submit"); return 0
    host = SITE.split("//")[1].split("/")[0]
    payload = {"host": host, "key": key, "keyLocation": f"{SITE}/{key}.txt", "urlList": batch}
    # make sure the key file is really being served before IndexNow checks it
    for _ in range(6):
        k = requests.get(f"{SITE}/{key}.txt", timeout=20)
        if k.status_code == 200 and k.text.strip() == key:
            break
        time.sleep(15)
    ok = False
    for ep in ("https://api.indexnow.org/indexnow", "https://www.bing.com/indexnow", "https://yandex.com/indexnow"):
        try:
            r = requests.post(ep, json=payload, headers={"Content-Type": "application/json; charset=utf-8"}, timeout=30)
            print("IndexNow", ep, r.status_code, len(batch), "urls", r.text[:200])
            if r.status_code in (200, 202):
                ok = True
                break
        except Exception as exc:
            print("IndexNow", ep, "error", exc)
    if ok:
        REC.parent.mkdir(exist_ok=True)
        REC.write_text(json.dumps(sorted(done | set(new))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
