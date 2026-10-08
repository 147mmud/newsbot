"""
Turn a raw feed item into a publishable article:
  * SEO headline   * category   * original summary (LLM) or attributed brief (offline)
  * slug, tags, meta description, key points

Two modes:
  1. LLM mode (free tier Groq / Gemini / HuggingFace): writes an original
     ~200-word brief from the facts in the feed item only.
  2. Offline mode (no API at all): builds an attributed news brief with
     extractive key points. These pages are marked `noindex` by default
     (see INDEX_OFFLINE_ARTICLES) so thin copies never hurt your SEO.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections import Counter

import config
from bot import llm

log = logging.getLogger("processor")

INDEX_OFFLINE_ARTICLES = config.env("INDEX_OFFLINE_ARTICLES", False, bool)

# --------------------------------------------------------------------------
# Categorisation
# --------------------------------------------------------------------------
KEYWORDS = {
    "politics": """election elections senate congress parliament minister president
        prime-minister white-house democrat democrats republican republicans gop labour
        tory tories conservative liberal vote voters ballot campaign governor bill law
        lawmakers policy supreme-court impeachment trump biden harris starmer trudeau
        carney merz bundestag downing-street mp mps referendum legislation""",
    "business": """market markets stock stocks shares economy economic inflation
        interest-rate rates fed federal-reserve bank banks earnings revenue profit
        profits company companies ceo merger acquisition ipo investors dow nasdaq
        s&p ftse dax oil prices tariff tariffs trade jobs unemployment gdp retail
        startup funding billion million crypto bitcoin""",
    "tech": """tech technology ai artificial-intelligence openai google apple microsoft
        meta amazon nvidia chip chips semiconductor software app apps iphone android
        smartphone cyber cybersecurity hack hackers data privacy startup robot robots
        robotics spacex tesla ev electric-vehicle internet social-media tiktok x twitter
        quantum gadget computer laptop""",
    "sports": """match game games season league cup champion championship final
        tournament coach player players team goal goals score scored win wins won
        defeat nfl nba mlb nhl premier-league champions-league fifa uefa world-cup
        cricket rugby tennis golf f1 formula-one olympics super-bowl touchdown
        quarterback striker wicket test innings wimbledon""",
    "world": """war conflict ukraine russia israel gaza iran china india
        un united-nations nato ceasefire refugees earthquake flood floods wildfire
        hurricane storm diplomat diplomatic embassy summit troops military attack
        protest protests crisis""",
}
KEYWORDS = {k: set(v.split()) for k, v in KEYWORDS.items()}


def categorize(title: str, summary: str, hint: str | None, tags: list[str]) -> str:
    text = f"{title} {title} {summary} {' '.join(tags)}".lower()
    words = re.findall(r"[a-z0-9&]+(?:[-'][a-z0-9]+)*", text)
    bigrams = {f"{a}-{b}" for a, b in zip(words, words[1:])}
    tokens = Counter(words)
    scores = {}
    for cat, kws in KEYWORDS.items():
        scores[cat] = sum(tokens[k] for k in kws if k in tokens) + sum(2 for k in kws if k in bigrams)
    if hint in scores:
        scores[hint] += 3  # the feed's own section is a strong signal
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else (hint or config.DEFAULT_CATEGORY)


# --------------------------------------------------------------------------
# Headlines
# --------------------------------------------------------------------------
SMALL_WORDS = set("a an and as at but by en for if in of on or the to v v. via vs vs. with from into nor per up".split())
SOURCE_SUFFIX = re.compile(r"\s*[-|–—:]\s*(BBC( News| Sport)?|CNN|NPR|Reuters|The Guardian|NYT|The New York Times|CNBC|ESPN|DW|CBC( News)?|TechCrunch|The Verge)\s*$", re.I)


def title_case(s: str) -> str:
    words = s.split()
    out = []
    for i, w in enumerate(words):
        core = w.strip("\"'‘’“”()[]")
        if core.isupper() and len(core) > 1:            # acronyms: NATO, AI
            out.append(w)
        elif any(c.isupper() for c in core[1:]):          # iPhone, McDonald
            out.append(w)
        elif i not in (0, len(words) - 1) and core.lower() in SMALL_WORDS:
            out.append(w.lower())
        else:
            out.append(re.sub(r"[A-Za-z]", lambda m: m.group(0).upper(), w, count=1))
    return " ".join(out)


def seo_headline(title: str) -> str:
    """Clean, accurate, Title-Cased headline (US style). No fake clickbait."""
    t = SOURCE_SUFFIX.sub("", title).strip()
    t = re.sub(r"\s+", " ", t).strip(" .")
    t = t.replace(" - ", " – ")
    return title_case(t)


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------
def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'“‘])", text.strip())
    return [p.strip() for p in parts if len(p.split()) >= 5]


def slugify(text: str, max_words: int = 9) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"a", "an", "the", "of", "to", "and", "in", "on", "for", "is", "at", "as", "by"}
    words = [w for w in words if w not in stop] or words
    return "-".join(words[:max_words]) or "story"


def meta_description(text: str, limit: int = 155) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "…"


def extract_tags(title: str, summary: str, source_tags: list[str]) -> list[str]:
    tags = [t for t in source_tags if 2 < len(t) < 30]
    # Proper-noun phrases from the title/summary (cheap NER)
    for m in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\b", f"{title}. {summary}"):
        phrase = m.group(1)
        if phrase.lower() not in SMALL_WORDS and len(phrase) > 3 and phrase not in tags:
            tags.append(phrase)
    seen, out = set(), []
    for t in tags:
        k = t.lower()
        if k not in seen and k not in ("the", "this", "that", "after", "new"):
            seen.add(k)
            out.append(t)
    return out[:8]


# --------------------------------------------------------------------------
# Offline brief (no API)
# --------------------------------------------------------------------------
INTROS = {
    "politics": "In a developing political story, {source} reports:",
    "world":    "In international news, {source} reports:",
    "business": "In business and markets news, {source} reports:",
    "tech":     "In technology news, {source} reports:",
    "sports":   "In sports news, {source} reports:",
}


def offline_article(item: dict, category: str) -> dict:
    summary = item["source_summary"] or item["source_title"]
    sentences = split_sentences(summary)
    key_points = sentences[:3] if len(sentences) > 1 else []
    body = [
        {"type": "p", "text": INTROS.get(category, INTROS["world"]).format(source=item["source"])},
        {"type": "quote", "text": summary, "cite": item["source"]},
    ]
    return {
        "headline": seo_headline(item["source_title"]),
        "body": body,
        "key_points": key_points,
        "meta_description": meta_description(summary),
        "category": category,
        "mode": "offline",
        "index": INDEX_OFFLINE_ARTICLES,
    }


# --------------------------------------------------------------------------
# LLM rewrite (free tiers)
# --------------------------------------------------------------------------
SYSTEM_PROMPT = """You are a careful wire-service news editor writing for readers in the
USA, UK, Canada, Germany and Switzerland. You write ORIGINAL short news briefs in
clear American English. Hard rules:
- Use ONLY facts present in the SOURCE text. Never invent quotes, numbers, names,
  dates or outcomes. If a detail is not in the source, leave it out.
- Do not copy sentences from the source; write in your own words.
- The headline must be accurate, specific and compelling (55-70 characters),
  Title Case, no clickbait, no ALL CAPS, no question bait, no emojis.
- Neutral, factual tone. Attribute claims ("according to ...").
Return ONLY a JSON object, no markdown."""

USER_TEMPLATE = """SOURCE ({source}, published {published}):
Title: {title}
Summary: {summary}

Allowed categories: {categories}

Return JSON with exactly these keys:
{{
 "headline": "SEO headline, 55-70 chars",
 "paragraphs": ["2 to 4 short paragraphs, 150-250 words total, original wording"],
 "key_points": ["2-3 one-sentence takeaways"],
 "why_it_matters": "one or two sentences of context for US/European readers, only if supported by the source, else empty string",
 "meta_description": "max 155 chars",
 "category": "one of the allowed categories",
 "tags": ["3-6 short topic tags"]
}}"""


def _parse_json(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def llm_article(item: dict, fallback_category: str) -> dict | None:
    if not llm.available():
        return None
    prompt = USER_TEMPLATE.format(
        source=item["source"], published=item["published"][:16].replace("T", " "),
        title=item["source_title"], summary=item["source_summary"] or "(no summary)",
        categories=", ".join(config.CATEGORIES),
    )
    raw = llm.chat(SYSTEM_PROMPT, prompt)
    data = _parse_json(raw) if raw else None
    if not data or not data.get("paragraphs") or not data.get("headline"):
        return None
    paragraphs = [p.strip() for p in data["paragraphs"] if isinstance(p, str) and p.strip()]
    if sum(len(p.split()) for p in paragraphs) < 60:
        return None  # too thin, fall back
    body = [{"type": "p", "text": p} for p in paragraphs]
    if data.get("why_it_matters"):
        body.append({"type": "h2", "text": "Why It Matters"})
        body.append({"type": "p", "text": data["why_it_matters"].strip()})
    cat = str(data.get("category", "")).lower().strip()
    if cat not in config.CATEGORIES:
        cat = fallback_category
    headline = re.sub(r"\s+", " ", str(data["headline"])).strip().strip('"')
    return {
        "headline": title_case(headline) if headline.islower() else headline,
        "body": body,
        "key_points": [k for k in data.get("key_points", []) if isinstance(k, str)][:3],
        "meta_description": meta_description(data.get("meta_description") or paragraphs[0]),
        "category": cat,
        "llm_tags": [t for t in data.get("tags", []) if isinstance(t, str)][:6],
        "mode": f"llm:{llm.active_provider_name()}",
        "index": True,
    }


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------
def process(item: dict) -> dict:
    category = categorize(item["source_title"], item["source_summary"],
                          item.get("category_hint"), item.get("source_tags", []))
    art = None
    try:
        art = llm_article(item, category)
    except Exception as exc:
        log.warning("LLM failed for %s: %s", item["source_url"], exc)
    if not art:
        art = offline_article(item, category)
    tags = art.pop("llm_tags", None) or extract_tags(SOURCE_SUFFIX.sub("", item["source_title"]), item["source_summary"],
                                                    item.get("source_tags", []))
    words = sum(len(b["text"].split()) for b in art["body"])
    return {
        **item,
        **art,
        "tags": tags,
        "slug": f"{slugify(art['headline'])}-{item['id'][:6]}",
        "reading_minutes": max(1, round(words / 220)),
    }
