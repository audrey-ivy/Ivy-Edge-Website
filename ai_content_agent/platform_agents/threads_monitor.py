"""
IvyEdge — Threads Monitor + Discovery

Two modes run together each day:

  1. REPLY MONITOR — Fetch replies to IvyEdge's own Threads posts and draft
     responses. Requires META_ACCESS_TOKEN + THREADS_USER_ID in .env.

  2. OUTBOUND DISCOVERY (Apify) — Search Threads for posts about IvyEdge's
     topics using Apify's threads-scraper actor. No Meta credentials needed.
     Requires APIFY_API_TOKEN in .env.

Required in .env:
  META_ACCESS_TOKEN=...   (for reply monitor)
  THREADS_USER_ID=...     (for reply monitor)
  APIFY_API_TOKEN=...     (for outbound discovery)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import anthropic
import requests
from dotenv import load_dotenv

from platform_agents import EngagementOpportunity

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

logger = logging.getLogger("ivyedge.threads")

META_ACCESS_TOKEN = os.getenv("META_ACCESS_TOKEN", "")
THREADS_USER_ID   = os.getenv("THREADS_USER_ID", "")
THREADS_BASE      = "https://graph.threads.net/v1.0"

MIN_RELEVANCE_SCORE  = 6.0
MAX_DISCOVERY_POSTS  = 30
RESULTS_PER_QUERY    = 8
SEEN_LOG = Path(__file__).parent.parent / "engagement_log" / "threads_seen.json"

# Search queries for outbound discovery
SEARCH_QUERIES = [
    # Pillar 1 — non-traditional income & credit
    "freelance income credit",
    "self employed loan",
    "1099 income",
    "career gap finance",
    "gig worker credit",
    "variable income mortgage",
    "contractor income",
    "side hustle income",
    # Pillar 6 — workplace flexibility & women in workforce
    "4 day work week",
    "return to office women",
    "remote work caregiving",
    "paid parental leave",
    "flexible work women",
    "women leaving workforce",
    "caregiver career",
    "childcare work",
]

SIGNAL_KEYWORDS = [
    # Pillar 1
    "1099", "freelance", "self employed", "self-employed", "gig",
    "career gap", "credit", "loan", "income", "contractor",
    "side hustle", "variable income", "career break",
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
        json.dumps({"seen_ids": list(seen)[-500:]}, indent=2),
        encoding="utf-8",
    )


def _has_signal(text: str) -> bool:
    t = text.lower()
    return any(kw in t for kw in SIGNAL_KEYWORDS)


# ---------------------------------------------------------------------------
# Mode 1: Fetch replies to IvyEdge's own Threads posts
# ---------------------------------------------------------------------------

def _check_credentials() -> bool:
    if not META_ACCESS_TOKEN or not THREADS_USER_ID:
        logger.info("META_ACCESS_TOKEN/THREADS_USER_ID not set — skipping reply monitor")
        return False
    return True


def _fetch_my_posts(limit: int = 20) -> list[dict]:
    url = f"{THREADS_BASE}/{THREADS_USER_ID}/threads"
    resp = requests.get(url, params={
        "fields":       "id,text,timestamp,permalink",
        "limit":        limit,
        "access_token": META_ACCESS_TOKEN,
    }, timeout=15)
    if not resp.ok:
        logger.warning("Failed to fetch Threads posts: %s", resp.text[:200])
        return []
    return resp.json().get("data", [])


def _fetch_replies(thread_id: str) -> list[dict]:
    url = f"{THREADS_BASE}/{thread_id}/replies"
    resp = requests.get(url, params={
        "fields":       "id,text,timestamp,username",
        "access_token": META_ACCESS_TOKEN,
    }, timeout=15)
    if not resp.ok:
        return []
    return resp.json().get("data", [])


def _fetch_all_replies(seen: set[str]) -> list[dict]:
    posts = _fetch_my_posts()
    all_replies = []
    for post in posts:
        replies = _fetch_replies(post["id"])
        for r in replies:
            if r.get("id") in seen or not r.get("text"):
                continue
            r["parent_post_text"] = post.get("text", "")[:300]
            r["parent_post_id"]   = post["id"]
            all_replies.append(r)
    return all_replies


# ---------------------------------------------------------------------------
# Mode 2: Outbound discovery via Apify threads-scraper
# ---------------------------------------------------------------------------

def _fetch_discovery_posts(seen: set[str]) -> list[dict]:
    try:
        from apify_client import ApifyClient
    except ImportError:
        logger.error("apify-client not installed — run: pip install apify-client")
        return []

    token = os.getenv("APIFY_API_TOKEN", "")
    if not token:
        logger.info("APIFY_API_TOKEN not set — skipping Threads outbound discovery")
        return []

    client = ApifyClient(token)
    posts: list[dict] = []
    seen_ids: set[str] = set()

    try:
        run = client.actor("apify/threads-scraper").call(
            run_input={
                "searchQueries": SEARCH_QUERIES,
                "maxItems":      RESULTS_PER_QUERY,
            },
            timeout_secs=300,
        )
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())
        logger.info("Threads Apify: %d raw items returned", len(items))

        for item in items:
            pid    = str(item.get("id") or item.get("postId") or "")
            text   = item.get("text") or item.get("caption") or ""
            author = item.get("username") or item.get("ownerUsername") or ""
            url    = item.get("url") or item.get("postUrl") or \
                     (f"https://www.threads.net/@{author}/post/{pid}" if author and pid else "")

            if not pid or pid in seen or pid in seen_ids:
                continue
            if not _has_signal(text):
                continue

            posts.append({
                "id":      pid,
                "url":     url,
                "author":  author,
                "text":    text[:600],
                "likes":   item.get("likesCount") or item.get("likes") or 0,
                "replies": item.get("repliesCount") or item.get("replies") or 0,
                "source":  "discovery",
            })
            seen_ids.add(pid)

            if len(posts) >= MAX_DISCOVERY_POSTS:
                break

    except Exception as e:
        logger.error("Threads Apify run failed: %s", e)

    logger.info("Threads discovery: %d candidate posts", len(posts))
    return posts


# ---------------------------------------------------------------------------
# Claude scoring
# ---------------------------------------------------------------------------

_REPLY_SYSTEM_PROMPT = """You are the community voice for IvyEdge, a pre-launch consumer finance
platform for women with non-traditional financial histories.

When responding to replies on IvyEdge's Threads posts:
- Prioritize: questions, personal stories, disagreements that need nuance
- Skip: pure validation ("great post!"), obvious spam, irrelevant comments
- Replies should be warm, specific, and genuinely helpful
- Never mention unreleased products
- Max 3 sentences — Threads is a conversational medium
- You can invite them to join the waitlist or newsletter if the conversation calls for it"""

_DISCOVERY_SYSTEM_PROMPT = """You are the community engagement voice for IvyEdge, a pre-launch
consumer finance platform for women with non-traditional financial histories
(freelancers, career returners, entrepreneurs with variable income).

IvyEdge's thesis:
- Career gaps don't make you a credit risk
- 1099 income is real income
- High earners with non-W-2 income deserve products that match their reality
- Companies that offer 4-day weeks, remote work, and caregiver support keep women in the workforce

Threads comment norms:
- 1-3 sentences, warm and specific
- No links, no product names, nothing promotional
- Can reference working in fintech/finance to signal credibility
- Should feel like a genuine reply from a thoughtful follower

Reshare guidance:
- Flag posts worth reposting — real stories, strong takes, compelling data"""


def _score_replies(replies: list[dict], client: anthropic.Anthropic) -> list[EngagementOpportunity]:
    if not replies:
        return []

    replies_text = "\n\n".join(
        f"REPLY {i+1} (id={r['id']}, from=@{r.get('username','?')}):\n"
        f"[On our post: \"{r.get('parent_post_text','')[:150]}...\"]\n"
        f"Their reply: {r.get('text','')}"
        for i, r in enumerate(replies)
    )

    prompt = f"""Below are {len(replies)} replies to IvyEdge's Threads posts.

For each, output:
{{
  "reply_id": "<id>",
  "score": <0-10>,
  "rationale": "<one sentence>",
  "suggested_reply": "<ready-to-post reply text if score >= 6, else empty string>"
}}

JSON array only. No prose, no fences.

{replies_text}"""

    msg = client.messages.create(
        model=os.getenv("IVYEDGE_MODEL", "claude-sonnet-4-6"),
        max_tokens=1500,
        system=_REPLY_SYSTEM_PROMPT,
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
        logger.warning("Claude returned unparseable JSON for Threads replies")
        return []

    reply_map = {r["id"]: r for r in replies}
    opportunities = []
    for item in scored:
        rid = item.get("reply_id", "")
        if item.get("score", 0) < MIN_RELEVANCE_SCORE:
            continue
        r = reply_map.get(rid, {})
        opp = EngagementOpportunity(
            platform="threads",
            post_id=rid,
            url=f"https://www.threads.net/t/{rid}",
            author=r.get("username", ""),
            content=r.get("text", ""),
            score=float(item.get("score", 0)),
            rationale=item.get("rationale", ""),
            suggested_comment=item.get("suggested_reply", ""),
            suggested_action="comment",
        )
        opportunities.append(opp)

    return sorted(opportunities, key=lambda o: o.score, reverse=True)


def _score_discovery(posts: list[dict], client: anthropic.Anthropic) -> list[EngagementOpportunity]:
    if not posts:
        return []

    posts_text = "\n\n".join(
        f"POST {i+1} (id={p['id']}, @{p['author']}, "
        f"{p['likes']} likes, {p['replies']} replies):\n{p['text'] or '(no text)'}"
        for i, p in enumerate(posts)
    )

    prompt = f"""Below are {len(posts)} Threads posts found via keyword search.

For each, output:
{{
  "post_id": "<id>",
  "score": <0-10 float>,
  "rationale": "<one sentence>",
  "suggested_action": "comment" | "reshare" | "comment_and_reshare" | "skip",
  "suggested_comment": "<if action includes comment and score >= 6: ready-to-post comment. Empty string otherwise.>"
}}

JSON array only. No prose, no fences.

High scores (≥6): Person sharing real experience with freelance income, career gaps,
credit issues, OR workplace flexibility/RTO/caregiving. Our comment adds genuine value.
Reshare if it's a compelling story or take worth amplifying to IvyEdge's audience.

Low scores (<6): Generic content, brand posts, or topics we can't add anything specific to.

POSTS:
{posts_text}"""

    msg = client.messages.create(
        model=os.getenv("IVYEDGE_MODEL", "claude-sonnet-4-6"),
        max_tokens=2500,
        system=_DISCOVERY_SYSTEM_PROMPT,
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
        logger.warning("Claude returned unparseable JSON for Threads discovery")
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
            platform="threads",
            post_id=pid,
            url=p.get("url", ""),
            author=p.get("author", ""),
            content=p.get("text", ""),
            score=float(item.get("score", 0)),
            rationale=item.get("rationale", ""),
            suggested_comment=item.get("suggested_comment", ""),
            suggested_action=action,
        )
        opportunities.append(opp)

    return sorted(opportunities, key=lambda o: o.score, reverse=True)


# ---------------------------------------------------------------------------
# Post a reply via Threads API
# ---------------------------------------------------------------------------

def post_reply(thread_id: str, text: str) -> Optional[str]:
    """Reply to a Threads post. Returns the new post URL or None."""
    if not _check_credentials():
        return None

    create_url = f"{THREADS_BASE}/{THREADS_USER_ID}/threads"
    resp = requests.post(create_url, data={
        "media_type":   "TEXT",
        "text":         text,
        "reply_to_id":  thread_id,
        "access_token": META_ACCESS_TOKEN,
    }, timeout=15)
    if not resp.ok:
        logger.error("Threads reply creation failed: %s", resp.text[:200])
        return None

    container_id = resp.json().get("id")
    publish_url  = f"{THREADS_BASE}/{THREADS_USER_ID}/threads_publish"
    pub_resp = requests.post(publish_url, data={
        "creation_id":  container_id,
        "access_token": META_ACCESS_TOKEN,
    }, timeout=15)
    if not pub_resp.ok:
        logger.error("Threads reply publish failed: %s", pub_resp.text[:200])
        return None

    post_id = pub_resp.json().get("id", "")
    return f"https://www.threads.net/t/{post_id}"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def discover(dry_run: bool = False) -> list[EngagementOpportunity]:
    """Run both reply monitor and outbound discovery. Returns all opportunities."""
    seen = _load_seen()
    all_opportunities: list[EngagementOpportunity] = []
    all_seen_ids: list[str] = []

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Mode 1: reply monitor (requires Meta credentials)
    if _check_credentials():
        replies = _fetch_all_replies(seen)
        if replies:
            logger.info("Threads: scoring %d new replies", len(replies))
            reply_opps = _score_replies(replies, client)
            all_opportunities.extend(reply_opps)
            all_seen_ids.extend(r["id"] for r in replies)
        else:
            logger.info("Threads: no new replies found")

    # Mode 2: outbound discovery (requires Apify)
    discovery_posts = _fetch_discovery_posts(seen)
    if discovery_posts:
        logger.info("Threads: scoring %d discovery posts", len(discovery_posts))
        discovery_opps = _score_discovery(discovery_posts, client)
        all_opportunities.extend(discovery_opps)
        all_seen_ids.extend(p["id"] for p in discovery_posts)
    else:
        logger.info("Threads: no new discovery posts found")

    if not dry_run and all_seen_ids:
        for sid in all_seen_ids:
            seen.add(sid)
        _save_seen(seen)

    logger.info("Threads: %d total opportunities", len(all_opportunities))
    return sorted(all_opportunities, key=lambda o: o.score, reverse=True)
