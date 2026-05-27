"""
Ivy Edge Trend Monitor

Monitors Google News RSS feeds for keyword spikes and breaking news relevant
to Ivy Edge's content pillars. Uses Claude to assess relevance and suggest
timely posts to insert into the editorial calendar.

Run manually:      python trend_monitor.py
Run and suggest:   python trend_monitor.py --suggest-posts
Add to calendar:   python trend_monitor.py --suggest-posts --add-to-calendar editorial_calendar.csv
"""

import argparse
import csv
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import time

import anthropic
import feedparser
from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ivyedge.trends")

# ---------------------------------------------------------------------------
# Google Trends — seed terms for rising query discovery
# ---------------------------------------------------------------------------

# These are starting points. pytrends finds what people are *actually* searching
# alongside these terms this week — those rising queries then drive News searches.
TRENDS_SEED_TERMS = [
    # Finance & credit
    "credit score",
    "personal loan",
    "women finance",
    "freelance income",
    "small business loan",
    "fintech lending",
    "FICO score",
    "mortgage approval",
    # Workforce & economy participation
    "women leaving workforce",
    "childcare cost",
    "maternity leave policy",
    "gender pay gap",
    "return to work women",
]

# ---------------------------------------------------------------------------
# Hardcoded fallback keywords — used when Trends is unavailable
# ---------------------------------------------------------------------------

WATCH_TOPICS = {
    "Pillar 1: Financial Education for Non-Traditional Paths": [
        "1099 income loan",
        "freelance income mortgage",
        "credit score self employed",
        "gig worker loan",
        "career gap credit",
        "caregiving career return",
    ],
    "Pillar 2: Demystifying Finance": [
        "FICO score change",
        "credit score update",
        "APR interest rate news",
        "alternative credit scoring",
        "credit bureau news",
    ],
    "Pillar 3: Real Stories": [
        "women financial barriers",
        "gender pay gap finance",
        "women small business loan denied",
    ],
    "Pillar 4: Tools & How-Tos": [
        "credit building tips 2026",
        "how to improve credit score",
        "personal loan tips",
    ],
    "Pillar 5: Industry Trends & Advocacy": [
        "AI banking 2026",
        "fintech women",
        "CFPB ruling 2026",
        "consumer finance regulation",
        "open banking news",
    ],
    "Pillar 6: Building Differently — How Companies Can Stop Pushing Women Out": [
        # Structural barriers — childcare, caregiving, flexibility
        "childcare cost workforce women",
        "caregiving career penalty women",
        "motherhood penalty workplace",
        "parental leave policy employer",
        "workplace flexibility women retention",
        # Leaving and returning
        "women leaving corporate America",
        "women leaving workforce 2026",
        "return to work program women",
        "career returner hiring",
        # Pay, hiring, and systemic bias
        "gender pay gap employer",
        "pay transparency law 2026",
        "women promotion bias workplace",
        "hiring discrimination women",
        "DEI rollback women workplace",
        # Structural economics
        "women economic participation barriers",
        "gender workforce gap policy",
        "eldercare women workforce",
        "sandwich generation women employment",
    ],
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
MAX_ARTICLES_PER_TOPIC = 5
RECENCY_DAYS = 7

# arXiv search — recent academic research relevant to Ivy Edge's pillars
ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_RECENCY_DAYS = 60   # research moves slower than news
MAX_ARXIV_PER_QUERY = 5

ARXIV_QUERIES = [
    # Finance & credit discrimination
    "lending discrimination gender",
    "credit score women fairness",
    "fintech algorithmic bias gender",
    "mortgage discrimination algorithmic underwriting",
    "consumer credit gender racial bias",
    "alternative credit scoring non-traditional income",
    "gig economy income financial access",
    "small business lending women minority",
    # Workforce participation & structural barriers
    "motherhood penalty labor market",
    "caregiving gender wage gap employment",
    "childcare cost women labor force participation",
    "parental leave gender employment outcomes",
    "women workforce re-entry barriers",
    "gender pay gap employer policy",
    "workplace flexibility women retention",
    "eldercare women labor supply",
    "DEI gender diversity corporate outcomes",
    "gender discrimination hiring promotion",
]


# ---------------------------------------------------------------------------
# Google Trends rising queries
# ---------------------------------------------------------------------------

def fetch_rising_queries() -> list[str]:
    """
    Pull rising search queries from Google Trends using pytrends.

    "Rising" means searches that spiked this week relative to their baseline —
    these are the terms people are suddenly looking up more than usual.

    Returns a deduplicated list of query strings.
    Falls back to [] if pytrends is not installed or Google rate-limits us.

    Install: pip install pytrends
    """
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.info(
            "pytrends not installed — skipping rising query discovery. "
            "Run: pip install pytrends"
        )
        return []

    rising: list[str] = []

    try:
        pt = TrendReq(
            hl="en-US", tz=300,
            timeout=(10, 30),
            retries=2,
            backoff_factor=1.0,
        )
        # Only query a few seeds per run to stay under Google's rate limit
        for seed in TRENDS_SEED_TERMS[:5]:
            try:
                pt.build_payload([seed], timeframe="now 7-d", geo="US")
                related = pt.related_queries()
                df = (related.get(seed) or {}).get("rising")
                if df is not None and not df.empty:
                    queries = df["query"].head(6).tolist()
                    logger.info("Google Trends rising for '%s': %s", seed, queries[:3])
                    rising.extend(queries)
                time.sleep(2.5)          # be polite — Google will 429 if you rush
            except Exception as inner:
                logger.debug("pytrends error for '%s': %s", seed, inner)
                time.sleep(5)            # back off before next seed
                continue

    except Exception as outer:
        logger.warning("pytrends session failed: %s — falling back to base keywords", outer)

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for q in rising:
        if q.lower() not in seen:
            seen.add(q.lower())
            deduped.append(q)

    logger.info("Google Trends: %d unique rising quer%s discovered",
                len(deduped), "y" if len(deduped) == 1 else "ies")
    return deduped


# ---------------------------------------------------------------------------
# Feed fetching
# ---------------------------------------------------------------------------

def fetch_news(query: str, max_items: int = MAX_ARTICLES_PER_TOPIC) -> list[dict]:
    url = GOOGLE_NEWS_RSS.format(query=quote_plus(query))
    feed = feedparser.parse(url)
    cutoff = datetime.utcnow() - timedelta(days=RECENCY_DAYS)
    articles = []
    for entry in feed.entries[:max_items * 2]:
        published = None
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            published = datetime(*entry.published_parsed[:6])
        if published and published < cutoff:
            continue
        articles.append({
            "title": entry.get("title", ""),
            "url": entry.get("link", ""),
            "published": published.isoformat() if published else "",
            "summary": entry.get("summary", "")[:300],
        })
        if len(articles) >= max_items:
            break
    return articles


def collect_all_news() -> dict[str, list[dict]]:
    """
    Collect news articles from Google News.

    Two passes:
    1. Hardcoded pillar-aligned queries — always runs, guarantees coverage.
    2. Rising queries from Google Trends — what people are *actually* searching
       this week. Results land in a separate bucket so Claude knows they came
       from real search behaviour, not our assumptions.
    """
    results: dict[str, list[dict]] = {}

    # ── Pass 1: pillar-aligned base queries ──────────────────────────────
    all_seen_urls: set[str] = set()
    for pillar, queries in WATCH_TOPICS.items():
        pillar_articles = []
        for query in queries:
            for a in fetch_news(query):
                if a["url"] not in all_seen_urls:
                    all_seen_urls.add(a["url"])
                    pillar_articles.append(a)
        results[pillar] = pillar_articles
        logger.info("%s: %d articles found", pillar[:40], len(pillar_articles))

    # ── Pass 2: Google Trends rising queries ─────────────────────────────
    rising_queries = fetch_rising_queries()
    if rising_queries:
        trending_articles = []
        for query in rising_queries[:12]:   # cap requests
            for a in fetch_news(query):
                if a["url"] not in all_seen_urls:
                    all_seen_urls.add(a["url"])
                    trending_articles.append(a)
        if trending_articles:
            results["🔍 Rising Search Terms (Google Trends)"] = trending_articles
            logger.info(
                "Trending searches: %d articles from %d rising quer%s",
                len(trending_articles), len(rising_queries),
                "y" if len(rising_queries) == 1 else "ies",
            )

    return results


# ---------------------------------------------------------------------------
# arXiv academic paper search
# ---------------------------------------------------------------------------

def fetch_arxiv(query: str, max_items: int = MAX_ARXIV_PER_QUERY) -> list[dict]:
    """Search arXiv for recent papers matching query. Returns list of paper dicts."""
    import urllib.request
    import xml.etree.ElementTree as ET

    params = (
        f"search_query=all:{quote_plus(query)}"
        f"&start=0&max_results={max_items * 2}"
        f"&sortBy=submittedDate&sortOrder=descending"
    )
    url = f"{ARXIV_API}?{params}"
    cutoff = datetime.utcnow() - timedelta(days=ARXIV_RECENCY_DAYS)

    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, timeout=6, context=ctx) as resp:
            xml_data = resp.read()
    except Exception as e:
        logger.warning("arXiv fetch failed for '%s': %s", query, e)
        return []

    ns = {
        "atom": "http://www.w3.org/2005/Atom",
        "arxiv": "http://arxiv.org/schemas/atom",
    }
    root = ET.fromstring(xml_data)
    papers = []

    for entry in root.findall("atom:entry", ns):
        published_str = (entry.findtext("atom:published", "", ns) or "")[:10]
        try:
            published = datetime.strptime(published_str, "%Y-%m-%d")
        except ValueError:
            continue
        if published < cutoff:
            continue

        arxiv_id = (entry.findtext("atom:id", "", ns) or "").split("/abs/")[-1]
        title    = (entry.findtext("atom:title", "", ns) or "").replace("\n", " ").strip()
        summary  = (entry.findtext("atom:summary", "", ns) or "").replace("\n", " ").strip()[:400]
        link     = f"https://arxiv.org/abs/{arxiv_id}"
        authors  = [
            a.findtext("atom:name", "", ns)
            for a in entry.findall("atom:author", ns)
        ]

        papers.append({
            "title":     title,
            "url":       link,
            "published": published_str,
            "summary":   summary,
            "authors":   ", ".join(authors[:4]),
            "arxiv_id":  arxiv_id,
        })
        if len(papers) >= max_items:
            break

    return papers


def collect_arxiv_papers() -> list[dict]:
    """Search all ARXIV_QUERIES and return deduplicated recent papers."""
    seen_ids: set[str] = set()
    all_papers: list[dict] = []

    for query in ARXIV_QUERIES:
        for paper in fetch_arxiv(query):
            if paper["arxiv_id"] not in seen_ids:
                seen_ids.add(paper["arxiv_id"])
                all_papers.append(paper)

    logger.info("arXiv: %d unique recent paper(s) found across %d queries",
                len(all_papers), len(ARXIV_QUERIES))
    return all_papers


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

def analyze_with_claude(
    news_by_pillar: dict[str, list[dict]],
    arxiv_papers: list[dict] | None = None,
) -> str:
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    news_text = ""
    for pillar, articles in news_by_pillar.items():
        if not articles:
            continue
        news_text += f"\n### {pillar}\n"
        for a in articles:
            news_text += f"- [{a['title']}]({a['url']}) — {a['published']}\n"
            if a["summary"]:
                news_text += f"  > {a['summary']}\n"

    papers_text = ""
    if arxiv_papers:
        papers_text = "\n\n---\n## Recent Academic Research (arXiv)\n"
        for p in arxiv_papers:
            papers_text += (
                f"\n### [{p['title']}]({p['url']})\n"
                f"- **Authors:** {p['authors']}  **Published:** {p['published']}\n"
                f"- {p['summary']}\n"
            )

    prompt = f"""You are the editorial strategist for Ivy Edge, a pre-launch consumer finance platform
built for women with non-traditional financial histories (freelancers, career returners, entrepreneurs).

Ivy Edge's content pillars:
1. Financial Education for Non-Traditional Paths — credit, loans, income documentation for freelancers/career returners
2. Demystifying Finance — plain-language explainers, debunking myths
3. Real Stories — women's lived financial experiences
4. Tools & How-Tos — practical guides and checklists
5. Industry Trends & Advocacy — fintech, regulation, systemic change
6. Building Differently — How Companies Can Stop Pushing Women Out
   This pillar covers the structural reasons women leave or never enter the workforce/economy:
   childcare costs, caregiving penalties, pay gaps, rigid hiring, lack of flexibility, DEI rollbacks,
   the motherhood penalty, eldercare burdens, return-to-work barriers. The Ivy Edge angle is always
   employer accountability and structural solutions — not individual advice to women on how to cope.

Here is what's in the news this week relevant to Ivy Edge's topics:
{news_text}
{papers_text}

Note: the "🔍 Rising Search Terms" section (if present) shows articles found via Google Trends
rising queries — actual searches that spiked this week. Weight these signals heavily: they reflect
what Ivy Edge's audience is already looking up and not finding good answers to.

Your job:
1. TRENDING NOW — identify 2–3 stories or papers that are genuinely timely and worth a fast-follow
   Ivy Edge post this week. Prioritise anything surfaced via rising search terms — those signal unmet
   demand. Academic papers count as timely if published within 60 days and the finding is directly
   actionable or validating for Ivy Edge's audience. For each, give: the hook, the Ivy Edge angle
   (our POV, not a summary), the best pillar, and the source URL to cite.
2. EVERGREEN SIGNAL — identify 1–2 patterns (across news or research) that suggest a topic worth
   adding to the calendar even if no single item is urgent.
3. SKIP — briefly note anything that looks relevant but isn't a fit.

OUTPUT FORMAT (markdown, concise — this is an internal editorial brief):

## Trending Now
### [Story/paper title / angle]
- **Hook:** ...
- **Ivy Edge angle:** ...
- **Pillar:** ...
- **Suggested post title:** ...
- **Cite:** [source URL]
- **Urgency:** [publish within X days]

## Evergreen Signals
- ...

## Skip (and why)
- ...
"""

    msg = client.messages.create(
        model=os.getenv("IVYEDGE_MODEL", "claude-sonnet-4-6"),
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text


def suggest_calendar_rows(analysis: str) -> list[dict]:
    """Ask Claude to convert the analysis into ready-to-add CSV rows."""
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    today = datetime.utcnow().date()
    prompt = f"""Based on this editorial analysis, generate CSV rows for any posts flagged as
'Trending Now' that should be added to the Ivy Edge editorial calendar.

Today's date: {today}

ANALYSIS:
{analysis}

Output ONLY a JSON array of objects with these exact keys (no prose, no markdown fence):
scheduled_date (YYYY-MM-DD, schedule 2-3 days from today for urgent posts),
title,
persona (Priya | Maya | Carmen | Dominique | All),
pillar (use the full pillar name),
primary_keyword,
secondary_keywords (pipe-separated),
format (educational | customer_story | behavioral | industry),
status (queued),
notes

Example:
[{{"scheduled_date":"2026-05-05","title":"...","persona":"All","pillar":"Pillar 5: Industry Trends & Advocacy","primary_keyword":"...","secondary_keywords":"kw1|kw2","format":"industry","status":"queued","notes":"Timely: triggered by [story]. CTA = newsletter."}}]
"""
    msg = client.messages.create(
        model=os.getenv("IVYEDGE_MODEL", "claude-sonnet-4-6"),
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    # Strip any accidental markdown fence
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning("Could not parse suggested rows: %s", e)
        return []


WEEKLY_CAP = 2  # max posts per week; trending posts displace scheduled ones


def add_rows_to_calendar(rows: list[dict], calendar_path: Path) -> None:
    """
    Insert trending posts into the calendar, capped at WEEKLY_CAP posts per week.

    If adding trending posts for the current week would push the week over the
    cap, existing queued posts for that week are bumped forward by 7 days to
    make room. Trending topics always take priority.
    """
    if not rows:
        return

    fieldnames = [
        "scheduled_date", "published_at", "title", "persona", "pillar",
        "primary_keyword", "secondary_keywords", "format", "status", "notes",
    ]

    # Load existing calendar
    existing: list[dict] = []
    if calendar_path.exists():
        with calendar_path.open(newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))

    existing_titles = {r.get("title", "").strip() for r in existing}

    # Deduplicate incoming trending rows
    new_rows = [r for r in rows if r.get("title", "").strip() not in existing_titles]
    if not new_rows:
        logger.info("No new trending rows to add (all already in calendar).")
        return

    # Define "this week" as today through today+6
    today = datetime.utcnow().date()
    week_end = today + timedelta(days=6)

    def _parse_date(s: str) -> date | None:
        try:
            return date.fromisoformat(s.strip())
        except (ValueError, AttributeError):
            return None

    # Find queued posts already scheduled this week
    this_week_queued = [
        r for r in existing
        if r.get("status", "").strip().lower() == "queued"
        and (d := _parse_date(r.get("scheduled_date", ""))) is not None
        and today <= d <= week_end
    ]

    # How many trending posts are targeting this week?
    trending_this_week = [
        r for r in new_rows
        if (d := _parse_date(r.get("scheduled_date", ""))) is not None
        and today <= d <= week_end
    ]

    slots_available = max(0, WEEKLY_CAP - len(trending_this_week))
    posts_to_bump   = max(0, len(this_week_queued) - slots_available)

    if posts_to_bump:
        # Bump the last N scheduled posts out by one week (FIFO — keep earliest)
        to_bump = sorted(
            this_week_queued,
            key=lambda r: r.get("scheduled_date", ""),
            reverse=True,           # bump the latest-dated ones first
        )[:posts_to_bump]

        bumped_titles = set()
        for r in to_bump:
            old_date = _parse_date(r["scheduled_date"])
            new_date = old_date + timedelta(days=7)
            r["scheduled_date"] = new_date.isoformat()
            r["notes"] = (r.get("notes", "") + " [bumped by trending topic]").strip()
            bumped_titles.add(r.get("title", "").strip())

        logger.info(
            "Bumped %d scheduled post(s) to next week to make room for trending content: %s",
            posts_to_bump,
            ", ".join(f'"{t}"' for t in bumped_titles),
        )

    # Append trending rows (cap to WEEKLY_CAP if somehow more than cap arrived)
    capped_new = new_rows[:WEEKLY_CAP]
    if len(new_rows) > WEEKLY_CAP:
        logger.info(
            "Capping trending posts to %d (dropped %d lower-priority suggestions)",
            WEEKLY_CAP, len(new_rows) - WEEKLY_CAP,
        )

    # Merge, re-sort by scheduled_date, reassign weekly dates so nothing overlaps.
    # Published articles always stay at the top with their published_at intact.
    all_rows = existing + capped_new
    published_rows   = [r for r in all_rows if r.get("status", "").strip().lower() == "published"]
    unpublished_rows = [r for r in all_rows if r.get("status", "").strip().lower() != "published"]

    unpublished_rows.sort(key=lambda r: r.get("scheduled_date", "9999"))

    # Find the first available Monday on or after today
    base = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    for i, row in enumerate(unpublished_rows):
        row["scheduled_date"] = (base + timedelta(weeks=i)).isoformat()
        row.setdefault("published_at", "")

    with calendar_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in published_rows + unpublished_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})

    logger.info(
        "Added %d trending post(s) to %s (weekly cap: %d)",
        len(capped_new), calendar_path, WEEKLY_CAP,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Ivy Edge trend monitor")
    parser.add_argument("--suggest-posts", action="store_true",
                        help="Use Claude to suggest timely posts from the news")
    parser.add_argument("--add-to-calendar", metavar="CSV",
                        help="Add suggested posts to this editorial calendar CSV")
    parser.add_argument("--output", metavar="FILE",
                        help="Save the trend brief to this file (default: print to stdout)")
    args = parser.parse_args()

    print("Fetching news...")
    news = collect_all_news()

    print("Searching arXiv for recent research...")
    papers = collect_arxiv_papers()

    total_news = sum(len(v) for v in news.values())
    if total_news == 0 and not papers:
        print("No recent news or research found. Try again later or check your network.")
        return 0

    print(f"Analyzing {total_news} articles + {len(papers)} papers with Claude...")
    analysis = analyze_with_claude(news, arxiv_papers=papers)

    if args.output:
        Path(args.output).write_text(analysis, encoding="utf-8")
        print(f"Brief saved to {args.output}")
    else:
        print("\n" + "=" * 60)
        print(analysis)
        print("=" * 60 + "\n")

    if args.suggest_posts:
        print("Generating calendar suggestions...")
        rows = suggest_calendar_rows(analysis)
        if rows:
            print(f"\nSuggested posts ({len(rows)}):")
            for r in rows:
                print(f"  {r['scheduled_date']} — {r['title']}")
            if args.add_to_calendar:
                add_rows_to_calendar(rows, Path(args.add_to_calendar))
        else:
            print("No urgent posts suggested this week.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
