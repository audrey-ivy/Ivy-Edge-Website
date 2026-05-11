"""
IvyEdge — Instagram Scraper (Apify)

Discovers public Instagram posts via hashtag search using Apify's
instagram-hashtag-scraper actor. Reliable, maintained, no Meta API needed.

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

logger = logging.getLogger("ivyedge.instagram_scraper")

MIN_RELEVANCE_SCORE = 6.0
MAX_POSTS_PER_RUN   = 30
RESULTS_PER_HASHTAG = 5

SEEN_LOG = Path(__file__).parent.parent / "engagement_log" / "instagram_scraper_seen.json"

HASHTAGS = [
    # Pillar 1 — non-traditional income & credit
    "freelancefinance",
    "selfemployedlife",
    "1099life",
    "careergap",
    "womenentrepreneurs",
    "creditbuilding",
    "solopreneur",
    "freelancerproblems",
    "returntowork",
    "gigeconomy",
    "womeninfinance",
    "independentcontractor",
    "sidehustlemoney",
    "mompreneurs",
    "financialindependence",
    # Pillar 6 — workplace flexibility & women in workforce
    "4dayworkweek",
    "remotework",
    "worklifebalance",
    "womenatwork",
    "flexiblework",
    "paidparentalleave",
    "workingmoms",
    "caregivers",
    "returntooffice",
    "womenleavingwork",
]

SIGNAL_KEYWORDS = [
    # Pillar 1
    "1099", "freelance", "self employed", "self-employed", "gig",
    "career gap", "credit", "loan", "income", "contractor",
    "side hustle", "variable income", "career break", "denied",
    "mortgage", "unstable income", "non traditional",
    # Pillar 6
    "4 day", "four day", "remote work", "work from home", "RTO",
    "return to office", "parental leave", "maternity", "caregiver",
    "childcare", "women leaving", "flexible work", "work life",
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

    # Run one actor call with all hashtags to minimise billable tasks
    try:
        run = client.actor("apify/instagram-hashtag-scraper").call(
            run_input={
                "hashtags":     HASHTAGS,
                "resultsLimit": RESULTS_PER_HASHTAG,
            },
            timeout_secs=300,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info("Instagram Apify: %d raw items returned", len(items))

        for item in items:
            pid     = str(item.get("id") or item.get("shortCode") or "")
            caption = item.get("caption") or item.get("text") or ""
            url     = item.get("url") or item.get("postUrl") or ""
            hashtag = (item.get("hashtags") or [""])[0] if item.get("hashtags") else ""
            author  = item.get("ownerUsername") or item.get("username") or ""

            if not pid or pid in seen or pid in seen_ids:
                continue
            if not _has_signal(caption) and not _has_signal(hashtag):
                continue

            posts.append({
                "id":       pid,
                "url":      url or f"https://www.instagram.com/p/{pid}/",
                "author":   author,
                "caption":  caption[:600],
                "hashtag":  hashtag,
                "likes":    item.get("likesCount") or item.get("likes") or 0,
                "comments": item.get("commentsCount") or item.get("comments") or 0,
            })
            seen_ids.add(pid)

            if len(posts) >= MAX_POSTS_PER_RUN:
                break

    except Exception as e:
        logger.error("Instagram Apify run failed: %s", e)

    logger.info("Instagram: %d candidate posts", len(posts))
    return posts


# ---------------------------------------------------------------------------
# Claude scoring + comment drafting
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the community engagement voice for IvyEdge, a pre-launch
consumer finance platform for women with non-traditional financial histories
(freelancers, career returners, entrepreneurs with variable income).

IvyEdge's thesis:
- Career gaps don't make you a credit risk
- 1099 income is real income
- High earners with non-W-2 income deserve products that match their reality
- Plain-language financial transparency is a baseline, not a feature
- Companies that support flexible work, remote work, and caregivers keep women in the workforce

Instagram comment norms:
- 1-3 sentences, warm and specific to the post
- No links, no product names, nothing promotional
- Can reference working in fintech/finance to signal credibility
- Emoji are fine if they fit the tone
- Should feel like a genuine comment from a smart follower

Reshare guidance:
- Flag posts worth reposting to IvyEdge's audience (original voices, real stories, good data)
- Reshare = amplify their message, not just acknowledge it"""


def _score_and_draft(posts: list[dict], client: anthropic.Anthropic) -> list[EngagementOpportunity]:
    if not posts:
        return []

    posts_text = "\n\n".join(
        f"POST {i+1} (id={p['id']}, @{p['author']}, #{p['hashtag']}, "
        f"{p['likes']} likes, {p['comments']} comments):\n{p['caption'] or '(no caption)'}"
        for i, p in enumerate(posts)
    )

    prompt = f"""Below are {len(posts)} Instagram posts found via hashtag search.

For each, output:
{{
  "post_id": "<id>",
  "score": <0-10 float>,
  "rationale": "<one sentence>",
  "suggested_action": "comment" | "reshare" | "comment_and_reshare" | "skip",
  "suggested_comment": "<if action includes comment and score >= 6: ready-to-post comment. Empty string otherwise.>"
}}

JSON array only. No prose, no markdown fences.

High scores (≥6): Creator is sharing a real experience with freelance income, career gaps,
credit struggles, variable-income stress, OR workplace flexibility/remote work/caregiver challenges.
Our comment adds genuine value. Reshare if it's a compelling original voice worth amplifying.

Low scores (<6): Generic motivational content, brand posts, or topics we can't add anything specific to.

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
        logger.warning("Claude returned unparseable JSON for Instagram scoring")
        return []

    post_map = {p["id"]: p for p in posts}
    opportunities = []
    for item in scored:
        pid = item.get("post_id", "")
        if item.get("score", 0) < MIN_RELEVANCE_SCORE:
            continue
        action = item.get("suggested_action", "comment")
        if action == "skip":
            continue
        p = post_map.get(pid, {})
        opp = EngagementOpportunity(
            platform="instagram",
            post_id=pid,
            url=p.get("url", ""),
            author=p.get("author", ""),
            content=p.get("caption", ""),
            hashtags=[p.get("hashtag", "")],
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
    """Scrape Instagram hashtags and return scored opportunities."""
    seen = _load_seen()
    posts = _fetch_posts(seen)
    if not posts:
        return []

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    opportunities = _score_and_draft(posts, client)

    if not dry_run:
        for p in posts:
            seen.add(p["id"])
        _save_seen(seen)

    logger.info("Instagram: %d posts worth engaging with", len(opportunities))
    return opportunities
