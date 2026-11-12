"""One story = one page.

Two articles are "the same story" when they share the rare, specific words
(names, places, events) of their headlines. Common words count for little,
so "Lula wins Brazil runoff" matches "Lula defeats Bolsonaro son in Brazil
election" but not an unrelated "Brazil approves 40-hour week".

`Matcher` scores pairs; `split()` groups articles into canonical pages (the
earliest one, so URLs Google already saw stay put) and duplicates that
should redirect there. Deterministic: the site builder runs it every build.
"""
import math
import re
from collections import Counter
from datetime import datetime, timezone
from difflib import SequenceMatcher

STOP = set("the a an of to in on for and or at by with from is are was were be as it its this that new says say said after over "
           "news live update how why what who will vs into out up us not has have had more than amid about their his her they "
           "you your we our can could would may might still just first latest report reports reportedly".split())
CLOSE_H, FAR_H = 48, 96          # hours: stricter match the further apart two pieces are
CLOSE_T, FAR_T = 0.35, 0.55
SPORT_EXTRA = 0.25               # sports headlines share many generic words (league, cup, opener...)
MAX_DUPS = 8                     # a cluster never swallows more than this many pages


def words(*texts: str) -> frozenset:
    out = set()
    for t in texts:
        for w in re.findall(r"[a-z0-9]+", (t or "").lower()):
            if w not in STOP and len(w) > 2:
                out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return frozenset(out)


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (t or "").lower())


def when(a: dict) -> datetime:
    try:
        return datetime.fromisoformat(a["published"].replace("Z", "+00:00"))
    except Exception:
        return datetime.now(timezone.utc)


class Matcher:
    def __init__(self, articles: list[dict]):
        self.n = max(len(articles), 1)
        self.df = Counter()
        for a in articles:
            for variant in self._variants(a):
                self.df.update(variant)

    @staticmethod
    def _variants(a: dict):
        v = [words(a.get("headline", ""))]
        if a.get("source_title"):
            v.append(words(a["source_title"]))
        return v

    def idf(self, w: str) -> float:
        return math.log(self.n / (1 + self.df.get(w, 0)))

    def prep(self, a: dict):
        vs = [(w, sum(self.idf(x) for x in w)) for w in self._variants(a) if w]
        return vs, norm(a.get("source_title") or a.get("headline", "")), when(a)

    def margin(self, pa, pb, extra: float = 0.0) -> float:
        """How far above the match threshold two articles are (<= 0 means "different stories")."""
        hours = abs((pa[2] - pb[2]).total_seconds()) / 3600
        if hours > FAR_H:
            return -1.0
        thr = (CLOSE_T if hours <= CLOSE_H else FAR_T) + extra
        best = -1.0
        for wa, ta in pa[0]:
            for wb, tb in pb[0]:
                inter = wa & wb
                if len(inter) < 2 or sum(1 for x in inter if self.idf(x) > 3.0) < 2:
                    continue
                best = max(best, sum(self.idf(x) for x in inter) / (min(ta, tb) or 1) - thr)
        a, b = pa[1], pb[1]
        if a and b and abs(len(a) - len(b)) < 40 and SequenceMatcher(None, a, b).ratio() >= 0.82:
            best = max(best, 1.0)
        return best

    def similar(self, pa, pb, extra: float = 0.0) -> bool:
        return self.margin(pa, pb, extra) >= 0

    def find(self, art: dict, pool: list[dict], prepped: dict | None = None):
        """The article in `pool` that best matches `art` (same category if known), else None."""
        pa = self.prep(art)
        best, who = 0.0, None
        for b in pool:
            if art.get("category") and b.get("category") != art["category"]:
                continue
            pb = (prepped or {}).get(b["id"]) or self.prep(b)
            extra = SPORT_EXTRA if (art.get("category") or b.get("category")) == "sports" else 0.0
            m = self.margin(pa, pb, extra)
            if m >= 0 and m >= best:
                best, who = m, b
        return who


def split(articles: list[dict]):
    """-> (canonical articles in original order, {duplicate_id: canonical_article})."""
    m = Matcher(articles)
    order = sorted(articles, key=when)                  # oldest first
    canon, dup_of, recent, counts = [], {}, [], Counter()                  # recent: [(prepped, article)]
    for a in order:
        pa = m.prep(a)
        recent = [r for r in recent if (pa[2] - r[0][2]).total_seconds() / 3600 <= FAR_H]
        extra = SPORT_EXTRA if a.get("category") == "sports" else 0.0
        hit = next((r[1] for r in recent if r[1].get("category") == a.get("category") and m.similar(pa, r[0], extra)), None)
        if hit is not None and counts[hit["id"]] < MAX_DUPS:
            counts[hit["id"]] += 1
            dup_of[a["id"]] = hit
        else:
            canon.append(a)
            recent.append((pa, a))
    keep = {a["id"] for a in canon}
    return [a for a in articles if a["id"] in keep], dup_of
