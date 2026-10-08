"""
NewsBot configuration.

Everything here can be overridden with environment variables (GitHub Actions
"Secrets" / "Variables"), so you never have to commit keys.
"""
import os

def env(name, default=None, cast=str):
    v = os.getenv(name)
    if v is None or v == "":
        return default
    if cast is bool:
        return v.strip().lower() in ("1", "true", "yes", "on")
    return cast(v)

# --------------------------------------------------------------------------
# SITE
# --------------------------------------------------------------------------
SITE_NAME = env("SITE_NAME", "The Daily Wire Desk")
SITE_TAGLINE = env("SITE_TAGLINE", "Breaking news from the US, UK, Canada & Europe")
# Full public URL, no trailing slash. For GitHub Pages project sites:
#   https://<user>.github.io/<repo>
# For a custom domain:  https://example.com
SITE_URL = env("SITE_URL", "https://example.github.io/newsbot").rstrip("/")
SITE_LANG = "en"
SITE_LOCALE = "en_US"

# Where the bot publishes: "static" (GitHub Pages / Netlify / Vercel),
# "wordpress", or "both".
PUBLISH_TARGET = env("PUBLISH_TARGET", "static")

# --------------------------------------------------------------------------
# PIPELINE LIMITS (keep these modest: free LLM tiers are rate-limited)
# --------------------------------------------------------------------------
MAX_NEW_PER_RUN = env("MAX_NEW_PER_RUN", 12, int)     # new articles per 30-min run
MAX_PER_FEED = env("MAX_PER_FEED", 6, int)            # newest N items read per feed
MAX_ARTICLE_AGE_HOURS = env("MAX_ARTICLE_AGE_HOURS", 24, int)
MAX_STORED_ARTICLES = env("MAX_STORED_ARTICLES", 3000, int)
DUPLICATE_TITLE_SIMILARITY = 0.82  # 0..1, higher = stricter dedupe
REQUEST_TIMEOUT = 20
USER_AGENT = f"Mozilla/5.0 (compatible; NewsBot/1.0; +{SITE_URL})"

# Show the thumbnail the publisher puts in its RSS feed (hotlinked, credited).
# Set to False to use the built-in category artwork instead.
USE_FEED_IMAGES = env("USE_FEED_IMAGES", True, bool)

# --------------------------------------------------------------------------
# CATEGORIES
# --------------------------------------------------------------------------
CATEGORIES = {
    "politics": "Politics",
    "world":    "World News",
    "business": "Business",
    "tech":     "Tech",
    "sports":   "Sports",
}
DEFAULT_CATEGORY = "world"

# --------------------------------------------------------------------------
# RSS FEEDS  (url, source name, category hint or None)
# All are public RSS endpoints offered by the publishers.
# Notes:
#  * Reuters retired its public RSS feeds in 2020 - there is no free official
#    Reuters feed any more, so it is not listed.
#  * CNN's rss.cnn.com feeds are rarely updated now; kept but optional.
# --------------------------------------------------------------------------
FEEDS = [
    # --- World / general ---
    ("https://feeds.bbci.co.uk/news/world/rss.xml",              "BBC News",      "world"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/World.xml",   "The New York Times", "world"),
    ("https://www.theguardian.com/world/rss",                    "The Guardian",  "world"),
    ("https://feeds.npr.org/1004/rss.xml",                       "NPR",           "world"),
    ("https://rss.dw.com/rdf/rss-en-all",                    "DW",            "world"),
    ("https://www.cbc.ca/webfeed/rss/rss-world",                 "CBC News",      "world"),
    ("http://rss.cnn.com/rss/edition_world.rss",                 "CNN",           "world"),
    # --- Politics ---
    ("https://feeds.bbci.co.uk/news/politics/rss.xml",           "BBC News",      "politics"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml","The New York Times", "politics"),
    ("https://feeds.npr.org/1014/rss.xml",                       "NPR",           "politics"),
    ("https://www.theguardian.com/us-news/us-politics/rss",      "The Guardian",  "politics"),
    # --- Business ---
    ("https://feeds.bbci.co.uk/news/business/rss.xml",           "BBC News",      "business"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Business.xml","The New York Times", "business"),
    ("https://www.cnbc.com/id/10001147/device/rss/rss.html",     "CNBC",          "business"),
    # --- Tech ---
    ("https://feeds.bbci.co.uk/news/technology/rss.xml",         "BBC News",      "tech"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml","The New York Times", "tech"),
    ("https://techcrunch.com/feed/",                             "TechCrunch",    "tech"),
    ("https://www.theverge.com/rss/index.xml",                   "The Verge",     "tech"),
    # --- Sports ---
    ("https://feeds.bbci.co.uk/sport/rss.xml",                   "BBC Sport",     "sports"),
    ("https://www.espn.com/espn/rss/news",                       "ESPN",          "sports"),
    ("https://rss.nytimes.com/services/xml/rss/nyt/Sports.xml",  "The New York Times", "sports"),
]

# --------------------------------------------------------------------------
# FREE LLM REWRITING (optional - all have free tiers, no card needed)
# The first provider whose key is set is used. If none are set, the bot uses
# the built-in offline summariser (no API at all).
#   GROQ_API_KEY    -> https://console.groq.com/keys
#   GEMINI_API_KEY  -> https://aistudio.google.com/apikey
#   HF_TOKEN        -> https://huggingface.co/settings/tokens
# Model names change over time - override with *_MODEL variables if needed.
# --------------------------------------------------------------------------
LLM_PROVIDERS = [
    {
        "name": "groq",
        "key_env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": env("GROQ_MODEL", "openai/gpt-oss-120b"),
    },
    {
        "name": "gemini",
        "key_env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": env("GEMINI_MODEL", "gemini-2.5-flash"),
    },
    {
        "name": "huggingface",
        "key_env": "HF_TOKEN",
        "url": "https://router.huggingface.co/v1/chat/completions",
        "model": env("HF_MODEL", "meta-llama/Llama-3.1-8B-Instruct"),
    },
]
LLM_SECONDS_BETWEEN_CALLS = env("LLM_SECONDS_BETWEEN_CALLS", 4, float)

# --------------------------------------------------------------------------
# WORDPRESS (only if PUBLISH_TARGET is "wordpress" or "both")
# Create an Application Password: WP Admin -> Users -> Profile ->
# "Application Passwords".
# --------------------------------------------------------------------------
WP_URL = env("WP_URL", "").rstrip("/")          # e.g. https://mynews.com
WP_USER = env("WP_USER", "")
WP_APP_PASSWORD = env("WP_APP_PASSWORD", "")
WP_POST_STATUS = env("WP_POST_STATUS", "publish")  # or "draft" to review first

# --------------------------------------------------------------------------
# LIVE SCORES - snapshot fetched by the bot (fallback for the header widget)
# The browser widget calls ESPN live first; this file is used if that fails.
# --------------------------------------------------------------------------
SCORE_SPORTS = {
    "football": "https://site.api.espn.com/apis/personalized/v2/scoreboard/header?sport=soccer&region=us&lang=en",
    "cricket":  "https://site.api.espn.com/apis/personalized/v2/scoreboard/header?sport=cricket&region=us&lang=en",
    "rugby":    "https://site.api.espn.com/apis/personalized/v2/scoreboard/header?sport=rugby&region=us&lang=en",
    "nfl":      "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard",
}

# --------------------------------------------------------------------------
# ADSTERRA - paste the code Adsterra gives you for each unit.
# Leave a value empty and that slot shows nothing (no empty grey boxes).
# Recommended: store these as GitHub "Variables" (not secrets - they are
# public in your HTML anyway) named ADSTERRA_HEADER_728 etc.
# --------------------------------------------------------------------------
ADS = {
    # Banner 728x90 (desktop header). Paste the full <script> pair from Adsterra.
    "header_728":    env("ADSTERRA_HEADER_728", ""),
    # Banner 320x50 (mobile header) - shown instead of 728x90 on phones.
    "header_320":    env("ADSTERRA_HEADER_320", ""),
    # Banner 300x250 (sidebar + in-article)
    "rect_300":      env("ADSTERRA_RECT_300", ""),
    # Banner 160x600 or 160x300 (sticky sidebar, desktop only)
    "sky_160":       env("ADSTERRA_SKY_160", ""),
    # Native Banner (script + container div, paste both)
    "native":        env("ADSTERRA_NATIVE", ""),
    # Social Bar (single <script> tag - goes before </body>)
    "social_bar":    env("ADSTERRA_SOCIAL_BAR", ""),
    # Popunder (single <script> tag - loaded after the page finishes)
    "popunder":      env("ADSTERRA_POPUNDER", ""),
    # Sticky footer banners (optional). Tip: on mobile use EITHER this OR the
    # Social Bar, they both sit at the bottom of the screen.
    "sticky_728":    env("ADSTERRA_STICKY_728", ""),
    "sticky_320":    env("ADSTERRA_STICKY_320", ""),
}
# Draw labelled dashed boxes where ads will go (for previewing the layout
# before you have Adsterra codes). Never enable in production.
ADS_PREVIEW = env("ADS_PREVIEW", False, bool)
