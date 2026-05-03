"""
IvyEdge Trend Monitor

Monitors Google News RSS feeds for keyword spikes and breaking news relevant
to IvyEdge's content pillars. Uses Claude to assess relevance and suggest
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
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import anthropic
import feedparser
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ivyedge.trends")

# ---------------------------------------------------------------------------
# Keywords to monitor — mapped to the pillar they belong to
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
}

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
MAX_ARTICLES_PER_TOPIC = 5
RECENCY_DAYS = 7


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
    results: dict[str, list[dict]] = {}
    for pillar, queries in WATCH_TOPICS.items():
        pillar_articles = []
        for query in queries:
            articles = fetch_news(query)
            for a in articles:
                if not any(x["url"] == a["url"] for x in pillar_articles):
                    pillar_articles.append(a)
        results[pillar] = pillar_articles
        logger.info("%s: %d articles found", pillar[:40], len(pillar_articles))
    return results


# ---------------------------------------------------------------------------
# Claude analysis
# ---------------------------------------------------------------------------

def analyze_with_claude(news_by_pillar: dict[str, list[dict]]) -> str:
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

    prompt = f"""You are the editorial strategist for IvyEdge, a pre-launch consumer finance platform
built for women with non-traditional financial histories (freelancers, career returners, entrepreneurs).

IvyEdge's five content pillars:
1. Financial Education for Non-Traditional Paths
2. Demystifying Finance
3. Real Stories
4. Tools & How-Tos
5. Industry Trends & Advocacy

Here is what's in the news this week relevant to IvyEdge's topics:

{news_text}

Your job:
1. TRENDING NOW — identify 2–3 stories that are genuinely timely and worth a fast-follow IvyEdge post
   this week. For each, give: the news hook, the IvyEdge angle (our POV, not a summary), and the best pillar.
2. EVERGREEN SIGNAL — identify 1–2 patterns across the news that suggest a topic IvyEdge should
   cover even if no single story is urgent. These are slow-burn insights worth adding to the calendar.
3. SKIP — briefly note any topics that look relevant but aren't a fit (wrong audience, wrong tone,
   would require us to take a side we shouldn't).

OUTPUT FORMAT (markdown, concise — this is an internal editorial brief):

## Trending Now
### [Story title / angle]
- **News hook:** ...
- **IvyEdge angle:** ...
- **Pillar:** ...
- **Suggested post title:** ...
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
'Trending Now' that should be added to the IvyEdge editorial calendar.

Today's date: {today}

ANALYSIS:
{analysis}

Output ONLY a JSON array of objects with these exact keys (no prose, no markdown fence):
publish_date (YYYY-MM-DD, schedule 2-3 days from today for urgent posts),
title,
persona (Priya | Maya | Carmen | Dominique | All),
pillar (use the full pillar name),
primary_keyword,
secondary_keywords (pipe-separated),
format (educational | customer_story | behavioral | industry),
status (queued),
notes

Example:
[{{"publish_date":"2026-05-05","title":"...","persona":"All","pillar":"Pillar 5: Industry Trends & Advocacy","primary_keyword":"...","secondary_keywords":"kw1|kw2","format":"industry","status":"queued","notes":"Timely: triggered by [story]. CTA = newsletter."}}]
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


def add_rows_to_calendar(rows: list[dict], calendar_path: Path) -> None:
    if not rows:
        return
    fieldnames = [
        "publish_date", "title", "persona", "pillar", "primary_keyword",
        "secondary_keywords", "format", "status", "notes",
    ]
    existing_titles = set()
    if calendar_path.exists():
        with calendar_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                existing_titles.add(row.get("title", "").strip())

    new_rows = [r for r in rows if r.get("title", "").strip() not in existing_titles]
    if not new_rows:
        logger.info("No new rows to add (all already in calendar).")
        return

    with calendar_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        for row in new_rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})
    logger.info("Added %d timely post(s) to %s", len(new_rows), calendar_path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="IvyEdge trend monitor")
    parser.add_argument("--suggest-posts", action="store_true",
                        help="Use Claude to suggest timely posts from the news")
    parser.add_argument("--add-to-calendar", metavar="CSV",
                        help="Add suggested posts to this editorial calendar CSV")
    parser.add_argument("--output", metavar="FILE",
                        help="Save the trend brief to this file (default: print to stdout)")
    args = parser.parse_args()

    print("Fetching news...")
    news = collect_all_news()

    total = sum(len(v) for v in news.values())
    if total == 0:
        print("No recent news found. Try again later or check your network.")
        return 0

    print(f"Analyzing {total} articles with Claude...")
    analysis = analyze_with_claude(news)

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
                print(f"  {r['publish_date']} — {r['title']}")
            if args.add_to_calendar:
                add_rows_to_calendar(rows, Path(args.add_to_calendar))
        else:
            print("No urgent posts suggested this week.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
