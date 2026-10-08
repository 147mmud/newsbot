# NewsBot: a free, automated news site

Every 30 minutes, GitHub Actions does the following:

1. Reads about 20 public RSS feeds (BBC, NYT, Guardian, NPR, DW, CBC, CNBC, TechCrunch, The Verge, ESPN, CNN).
2. Removes duplicates, including the same story from different outlets.
3. Writes a short original brief with a free LLM tier (Groq, Gemini or HuggingFace). If no key is set, it builds an attributed brief offline.
4. Writes an accurate Title Case SEO headline and sorts the story into **Politics, World News, Business, Tech or Sports**.
5. Builds a static site with sitemap, Google News sitemap, RSS, JSON-LD and OpenGraph, and deploys it to **GitHub Pages**. It can also post to **WordPress** through the REST API.
6. Saves a live-scores snapshot that the header widget uses as a fallback.

Running cost: **$0**, as long as the repository is **public**. Public repos get unlimited Actions minutes. A private repo gets 2,000 minutes a month, and this schedule uses about 2,500–3,000.

```
config.py               all settings (feeds, categories, LLM, WordPress, Adsterra)
main.py                 entry point
bot/fetcher.py          RSS fetching + normalising
bot/processor.py        headline, category, rewrite (LLM or offline), tags, slug
bot/llm.py              free-tier LLM client with fallback between providers
bot/store.py            data/articles.json + duplicate detection
bot/site_builder.py     static site generator (Jinja2)
bot/wordpress.py        WordPress REST publisher
bot/scores.py           ESPN scores snapshot
templates/              HTML templates (layout, ad slots, cards)
static/css/style.css    theme (mobile-first, no web fonts)
static/js/scores.js     live scores header widget
static/js/ads.js        Adsterra lazy loader (no layout shift)
.github/workflows/newsbot.yml   cron every 30 min + Pages deploy
```

---

## 1. Put the code on GitHub (5 min)

1. Create a free account at github.com, then **New repository**. Name it, for example, `newsbot`, and set it to **Public**.
2. Upload every file in this folder, including the hidden `.github` folder. Two ways to do it:
   - In the browser, use **Add file → Upload files** and drag the folder contents in. Make sure `.github/workflows/newsbot.yml` gets uploaded.
   - Or use git:
     ```bash
     git init && git add . && git commit -m "NewsBot"
     git branch -M main
     git remote add origin https://github.com/YOURNAME/newsbot.git
     git push -u origin main
     ```

## 2. Turn on GitHub Pages

Go to **Settings → Pages → Build and deployment → Source** and choose **GitHub Actions**.

## 3. Add your settings

Go to **Settings → Secrets and variables → Actions**.

**Variables** tab (these values aren't secret):

| Name | Example |
|---|---|
| `SITE_URL` | `https://yourname.github.io/newsbot` (or `https://yourdomain.com`) |
| `SITE_NAME` | `The Daily Wire Desk` |
| `SITE_TAGLINE` | `Breaking news from the US, UK, Canada & Europe` |
| `CONTACT_EMAIL` | `editor@yourdomain.com` |
| `PUBLISH_TARGET` | `static` (default), `wordpress`, or `both` |
| `ADSTERRA_HEADER_728`, `ADSTERRA_HEADER_320`, `ADSTERRA_RECT_300`, `ADSTERRA_SKY_160`, `ADSTERRA_NATIVE`, `ADSTERRA_SOCIAL_BAR`, `ADSTERRA_POPUNDER`, `ADSTERRA_STICKY_728`, `ADSTERRA_STICKY_320` | paste the full code Adsterra gives you for each unit |

**Secrets** tab (all optional):

| Name | Where to get it (free) |
|---|---|
| `GROQ_API_KEY` | https://console.groq.com/keys |
| `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| `HF_TOKEN` | https://huggingface.co/settings/tokens |
| `WP_USER`, `WP_APP_PASSWORD` | WordPress → Users → Profile → Application Passwords |

## 4. Start it

Go to **Actions → NewsBot → Run workflow**. After about 2 minutes your site is live at `SITE_URL`. From then on it runs at :07 and :37 past every hour, so you don't need a computer switched on.

Things to know about GitHub's scheduler:
- Scheduled runs can start 5–20 minutes late when GitHub is busy. That's normal.
- GitHub turns off schedules in a public repo after **60 days without repository activity**. The bot's own commits normally count as activity. If GitHub ever emails you that the workflow was disabled, open Actions and click **Enable workflow**.
- Backup trigger (optional): at cron-job.org, create a job that POSTs to
  `https://api.github.com/repos/YOURNAME/newsbot/actions/workflows/newsbot.yml/dispatches`
  with header `Authorization: Bearer <fine-grained token with Actions: write>`, header `Accept: application/vnd.github+json`, and body `{"ref":"main"}`.

---

## Adsterra setup

1. Sign up as a Publisher at Adsterra and add your site URL. A custom domain gets approved more easily than `github.io`. A `.com` costs about $10 a year; it's the only cost worth paying for.
2. Create these units. Each one gives you a code snippet to paste into the matching Variable:

| Slot on the page | Adsterra unit | Variable |
|---|---|---|
| Header (desktop) | Banner 728×90 | `ADSTERRA_HEADER_728` |
| Header (phones) | Banner 320×50 | `ADSTERRA_HEADER_320` |
| Inside the article (after paragraph 1), in the feed, top of sidebar, after the article | Banner 300×250 | `ADSTERRA_RECT_300` |
| Sticky sidebar (desktop only) | Banner 160×600 | `ADSTERRA_SKY_160` |
| After the hero, after the article, in category grids | Native Banner | `ADSTERRA_NATIVE` |
| Floating | Social Bar | `ADSTERRA_SOCIAL_BAR` |
| On click | Popunder | `ADSTERRA_POPUNDER` |
| Sticky footer (with a close ×) | Banner 728×90 / 320×50 | `ADSTERRA_STICKY_728` / `ADSTERRA_STICKY_320` |

How the slots keep pages fast:
- Each banner loads in its own lazy iframe once it is about 300px from the viewport, and its space is reserved in advance, so ads cause **no layout shift**. Several 300×250s on one page don't conflict, because each iframe has its own `atOptions`.
- The Social Bar and Popunder load 1.5 seconds after the page finishes, so they don't slow down Largest Contentful Paint.
- A slot with no code is not rendered, so you never get empty grey boxes.
- To preview the layout before you have any codes, add the Variable `ADS_PREVIEW=1`. This draws dashed placeholder boxes. Remove it before going live.
- On phones, use **either** the Social Bar **or** the sticky 320×50. Both sit at the bottom of the screen and would overlap.

## Live scores widget

The bar at the top shows Football, Cricket, Rugby and NFL. It reads ESPN's public scoreboard JSON straight from the visitor's browser, with no API key. While a match is live it refreshes every 60 seconds; otherwise every 5 minutes. It pauses when the browser tab is hidden. If ESPN can't be reached, it shows the `data/scores.json` snapshot the bot saved on its last run.
Endpoints are listed in `config.py → SCORE_SPORTS`. They are unofficial and could change; if one breaks, only that tab shows "No matches", and the rest of the site is unaffected.

## WordPress instead of (or as well as) the static site

Set `PUBLISH_TARGET=wordpress` (or `both`), plus the Variable `WP_URL` and the Secrets `WP_USER` and `WP_APP_PASSWORD`. Posts go to `/wp-json/wp/v2/posts`, and categories and tags are created automatically. For ads on WordPress, use the free **Ad Inserter** plugin and paste the same Adsterra codes. Use **Header Footer Code Manager** for the score bar: paste in the `scorebar` HTML from `templates/base.html`, the CSS, and `scores.js`.
Note: free WordPress hosts often block REST API calls from bots and disallow ad scripts, and WordPress.com free doesn't allow third-party ads. Realistically, the WordPress path needs cheap paid hosting. The static path stays free.

## Run locally

```bash
pip install -r requirements.txt
ADS_PREVIEW=1 SITE_URL=http://localhost:8000 python main.py
cd public && python -m http.server 8000
```

---

## Content, copyright & SEO

- The bot only uses what publishers put in their **RSS feeds** (headline and short summary). It doesn't scrape full articles. Every page credits the source and links to it.
- **LLM mode** writes an original 150–250 word brief using only facts from the feed item. The prompt forbids invented quotes and numbers.
- **Offline mode** (no key) shows the feed summary as a credited quote. That isn't unique content, so those pages get `noindex` by default (`INDEX_OFFLINE_ARTICLES=false`). This keeps thin pages from dragging down your site in Google. Setting a free Groq or Gemini key gets you indexable pages.
- Google's spam policies target "scaled content abuse", meaning mass-produced pages with no added value. To build traffic that lasts, add something on top of the automation: a few hand-written stories a week, topic pages, and better categorisation for your target countries.
- If a publisher asks you to stop, delete its line from `FEEDS` in `config.py`.
- Reuters ended its public RSS feeds in 2020, so it isn't included. CNN's RSS is rarely updated.
- The privacy page mentions ad cookies. Visitors from the EU, UK and Switzerland may need a cookie-consent banner under GDPR. Free CMPs exist, such as Cookiebot's free tier for small sites or Klaro (open source).
