"""
Reddit poster for IvyEdge.

Posts link submissions to relevant subreddits based on content pillar and persona.
Uses PRAW (Python Reddit API Wrapper).

Required .env variables:
    REDDIT_CLIENT_ID      — from reddit.com/prefs/apps
    REDDIT_CLIENT_SECRET  — from reddit.com/prefs/apps
    REDDIT_USERNAME       — JoinIvyEdge
    REDDIT_PASSWORD       — account password
"""

import logging
import os
import time
from pathlib import Path

import praw
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

logger = logging.getLogger("ivyedge.reddit")

# ---------------------------------------------------------------------------
# Subreddit mapping by pillar / persona
# ---------------------------------------------------------------------------

# Always post to these
BASE_SUBREDDITS = ["personalfinance"]

# Pillar-specific subreddits
PILLAR_SUBREDDITS: dict[str, list[str]] = {
    "Pillar 1": ["freelance", "selfemployed", "credit"],
    "Pillar 2": ["personalfinance", "credit", "financialindependence"],
    "Pillar 3": ["personalfinance", "financialindependence"],
    "Pillar 4": ["smallbusiness", "Entrepreneur"],
    "Pillar 5": ["personalfinance", "CreditCards"],
    "Pillar 6": ["TwoXChromosomes", "workingmoms", "careerguidance"],
}

# Persona-specific subreddits
PERSONA_SUBREDDITS: dict[str, list[str]] = {
    "Maya":      ["freelance", "selfemployed", "digitalnomad"],
    "Priya":     ["careerguidance", "TwoXChromosomes", "workingmoms"],
    "Carmen":    ["smallbusiness", "Entrepreneur"],
    "Dominique": ["credit", "personalfinance"],
    "All":       ["FemaleLevelUpStrategy"],
}

MAX_SUBREDDITS = 3   # cap per post to avoid spam flags


def _get_reddit() -> praw.Reddit:
    client_id     = os.getenv("REDDIT_CLIENT_ID", "")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
    username      = os.getenv("REDDIT_USERNAME", "JoinIvyEdge")
    password      = os.getenv("REDDIT_PASSWORD", "")

    if not all([client_id, client_secret, password]):
        raise ValueError(
            "Missing Reddit credentials. Set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, "
            "and REDDIT_PASSWORD in .env"
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        username=username,
        password=password,
        user_agent=f"IvyEdge content bot v1.0 (u/{username})",
    )


def _select_subreddits(pillar: str, persona: str) -> list[str]:
    """Pick the most relevant subreddits for this post, deduplicated and capped."""
    subs: list[str] = []

    # Pillar match (check prefix e.g. "Pillar 1: Financial Education...")
    for key, sub_list in PILLAR_SUBREDDITS.items():
        if pillar.startswith(key):
            subs.extend(sub_list)
            break

    # Persona match
    for key, sub_list in PERSONA_SUBREDDITS.items():
        if key.lower() in persona.lower() or persona == "All":
            subs.extend(sub_list)
            if persona != "All":
                break

    # Always include base, deduplicate, keep order
    seen = set()
    ordered = []
    for s in BASE_SUBREDDITS + subs:
        if s not in seen:
            seen.add(s)
            ordered.append(s)

    return ordered[:MAX_SUBREDDITS]


def post_to_reddit(
    title: str,
    body: str,
    url: str,
    pillar: str = "",
    persona: str = "",
) -> list[str]:
    """
    Submit a link post to each selected subreddit.
    Returns list of Reddit post URLs that succeeded.
    """
    reddit   = _get_reddit()
    subreddits = _select_subreddits(pillar, persona)
    posted_urls: list[str] = []

    for sub_name in subreddits:
        try:
            subreddit = reddit.subreddit(sub_name)
            submission = subreddit.submit(title=title, url=url)
            post_url = f"https://www.reddit.com{submission.permalink}"
            logger.info("Reddit posted to r/%s: %s", sub_name, post_url)
            posted_urls.append(post_url)
            # Reddit rate-limits submissions — wait between posts
            time.sleep(4)
        except Exception as e:
            logger.warning("Reddit r/%s failed: %s", sub_name, e)

    return posted_urls
