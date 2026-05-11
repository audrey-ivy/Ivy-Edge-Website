"""
IvyEdge — X (Twitter) Engagement Agent (Apify)

Discovers posts on X via keyword search using Apify's Twitter scraper actor.
Scores them with Claude and queues suggested replies for manual posting.

Required in .env:
    APIFY_API_TOKEN=...

Setup:
    pip install apify-client
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import anthropic
from dotenv import load_dotenv

from platform_agents import EngagementOpportunity

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

logger = logging.getLogger("ivyedge.x")

MIN_RELEVANCE_SCORE = 6.0
MAX_POSTS_PER_RUN   = 30
RESULTS_PER_QUERY   = 10

SEEN_LOG     = Path(__file__).parent.parent / "engagement_log" / "x_seen.json"
COOKIES_FILE = Path(__file__).parent.parent / "engagement_log" / "x_cookies.json"

SEARCH_QUERIES = [
    # Pillar 1 — non-traditional income & credit
    "1099 income credit OR loan",
    "freelance income denied mortgage OR loan",
    "self employed credit score",
    "career gap credit OR loan",
    "gig worker loan denied",
    "variable income bank OR mortgage",
    "non traditional income finance",
    "side hustle income credit",
    "maternity leave credit OR loan",
    "independent contractor income loan",
    # Pillar 6 — workplace flexibility & women in workforce
    "4 day work week women",
    "return to office women quit",
    "RTO mandate women leaving",
    "remote work caregiving women",
    "flexible work women workforce",
    "paid parental leave US",
    "caregiver penalty career women",
    "women leaving workforce childcare",
    "4 day week productivity",
    "student loans women workforce",
]

SIGNAL_KEYWORDS = [
    # Pillar 1
    "1099", "freelance", "self employed", "self-employed", "gig work",
    "career gap", "credit score", "loan denied", "side hustle", "contractor",
    "variable income", "non traditional", "career break", "mortgage denied",
    "unstable income", "independent contractor",
    # Pillar 6
    "4 day", "four day", "remote work", "work from home", "RTO",
    "return to office", "parental leave", "maternity", "caregiver",
    "childcare", "women leaving", "flexible work",
]


def _load_seen() -> set[str]:
    if SEEN_LOG.exists():
        data = json.loads(SEEN_LOG.read_text(encoding="utf-8"))
        return set(data.get("seen_ids", []))
    return set()


def _save_seen(seen: set[str]) -> None:
    SEEN_LOG.parent.mkdir(parents=True, exist_ok=True)
    SEEN_LOG.write_text(
        json.dumps({"seen_ids": list(seen)[-1000:]}, indent=2),
        encoding="utf-8",
    )


def _has_signal(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SIGNAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Apify fetch
# ---------------------------------------------------------------------------

def _fetch_posts(seen: set[str]) -> list[dict]:
    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.error("apify-client not installed — run: pip install apify-client")
        return []

    token = os.getenv("APIFY_API_TOKEN", "")
    if not token:
        logger.error("APIFY_API_TOKEN not set in .env")
        return []

    client = ApifyClient(token)
    posts: list[dict] = []
    seen_ids: set[str] = set()

    try:
        run = client.actor("apify/twitter-scraper").call(
            run_input={
                "searchTerms":      SEARCH_QUERIES,
                "maxItems":         RESULTS_PER_QUERY,
                "tweetLanguage":    "en",
                "onlyVerifiedUsers": False,
                "onlyTwitterBlue":   False,
            },
            timeout_secs=300,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info("X Apify: %d raw items returned", len(items))

        for item in items:
            # Handle both field name variants across actor versions
            tid    = str(item.get("id") or item.get("tweetId") or item.get("tweet_id") or "")
            text   = item.get("text") or item.get("full_text") or item.get("fullText") or ""
            author = (item.get("author") or {})
            screen = author.get("userName") or author.get("screen_name") or \
                     item.get("authorName") or item.get("username") or ""
            url    = item.get("url") or item.get("tweetUrl") or \
                     (f"https://x.com/{screen}/status/{tid}" if screen and tid else "")

            if not tid or tid in seen or tid in seen_ids:
                continue
            if not _has_signal(text):
                continue

            posts.append({
                "id":       tid,
                "url":      url,
                "author":   screen,
                "text":     text[:600],
                "likes":    item.get("likeCount") or item.get("likes") or item.get("favorite_count") or 0,
                "reposts":  item.get("retweetCount") or item.get("retweets") or item.get("retweet_count") or 0,
                "replies":  item.get("replyCount") or item.get("replies") or item.get("reply_count") or 0,
            })
            seen_ids.add(tid)

            if len(posts) >= MAX_POSTS_PER_RUN:
                break

    except Exception as e:
        logger.error("X Apify run failed: %s", e)

    logger.info("X: %d candidate posts", len(posts))
    return posts


# ---------------------------------------------------------------------------
# Claude scoring + reply drafting
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the community engagement voice for IvyEdge, a pre-launch
consumer finance platform for women with non-traditional financial histories
(freelancers, career returners, entrepreneurs with variable income).

IvyEdge's thesis:
- Career gaps don't make you a credit risk
- 1099 income is real income
- High earners with non-W-2 income deserve products that match their reality
- Plain-language financial transparency is a baseline, not a feature
- Companies that offer 4-day weeks, remote work, and caregiver support keep women in the workforce

X (Twitter) reply norms:
- 1-2 sentences max — X is a brevity-first platform
- Warm, specific, and directly responsive to what they said
- No links, no product names, no "we're building something"
- Can reference working in fintech to signal credibility
- Should feel like a genuine reply from a smart follower
- Emojis fine if they fit the tone — don't force them

Reshare guidance:
- Flag posts worth quote-tweeting to IvyEdge's audience (compelling data, real stories, strong takes)"""


def _score_and_draft(posts: list[dict], client: anthropic.Anthropic) -> list[EngagementOpportunity]:
    if not posts:
        return []

    posts_text = "\n\n".join(
        f"POST {i+1} (id={p['id']}, @{p['author']}, "
        f"{p['likes']} likes, {p['reposts']} reposts):\n{p['text'] or '(no text)'}"
        for i, p in enumerate(posts)
    )

    prompt = f"""Below are {len(posts)} X (Twitter) posts found via keyword search.

For each, output:
{{
  "post_id": "<id>",
  "score": <0-10 float>,
  "rationale": "<one sentence>",
  "suggested_action": "reply" | "reshare" | "reply_and_reshare" | "skip",
  "suggested_comment": "<if action includes reply and score >= 6: ready-to-post reply. Empty string otherwise.>"
}}

JSON array only. No prose, no markdown fences.

High scores (≥6): Person sharing real experience with 1099/freelance income, career gaps,
credit issues, OR workplace flexibility/RTO/caregiving. Our reply adds genuine value.
Reshare (quote-tweet) if it's a strong take or data point worth amplifying.

Low scores (<6): Generic finance content, brand posts, venting with no opening for engagement.

POSTS:
{posts_text}"""

    msg = client.messages.create(
        model=os.getenv("IVYEDGE_MODEL", "claude-sonnet-4-6"),
        max_tokens=3000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = msg.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        if raw.rstrip().endswith("```"):
            raw = raw.rstrip()[:-3]

    try:
        scored = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Claude returned unparseable JSON for X scoring")
        return []

    post_map = {p["id"]: p for p in posts}
    opportunities = []
    for item in scored:
        pid = item.get("post_id", "")
        if item.get("score", 0) < MIN_RELEVANCE_SCORE:
            continue
        action = item.get("suggested_action", "reply")
        if action == "skip":
            continue
        p = post_map.get(pid, {})
        opp = EngagementOpportunity(
            platform="x",
            post_id=pid,
            url=p.get("url", ""),
            author=p.get("author", ""),
            content=p.get("text", ""),
            hashtags=[],
            score=float(item.get("score", 0)),
            rationale=item.get("rationale", ""),
            suggested_comment=item.get("suggested_comment", ""),
            suggested_action=action,
        )
        opportunities.append(opp)

    return sorted(opportunities, key=lambda o: o.score, reverse=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def discover(dry_run: bool = False) -> list[EngagementOpportunity]:
    """Search X for relevant posts and return scored opportunities."""
    seen = _load_seen()
    posts = _fetch_posts(seen)
    if not posts:
        logger.info("X: no new posts found")
        return []

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    opportunities = _score_and_draft(posts, client)

    if not dry_run:
        for p in posts:
            seen.add(p["id"])
        _save_seen(seen)

    logger.info("X: %d posts worth engaging with", len(opportunities))
    return opportunities
