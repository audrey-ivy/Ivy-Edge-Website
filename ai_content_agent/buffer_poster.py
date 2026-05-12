"""
IvyEdge Buffer Poster — Instagram, Threads, TikTok

Posts branded content via the Buffer GraphQL API.
No Facebook account required — Buffer handles platform auth.

Required in .env:
  BUFFER_API_KEY=...              From buffer.com/account/access-token
  BUFFER_IG_CHANNEL_ID=...        Run --list-channels to find these
  BUFFER_THREADS_CHANNEL_ID=...
  BUFFER_TIKTOK_CHANNEL_ID=...

Usage:
  python buffer_poster.py --list-channels
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import requests
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)

logger = logging.getLogger("ivyedge.buffer")

BUFFER_API_KEY            = os.getenv("BUFFER_API_KEY", "")
BUFFER_ORG_ID             = os.getenv("BUFFER_ORG_ID", "")
BUFFER_IG_CHANNEL_ID      = os.getenv("BUFFER_IG_CHANNEL_ID", "")
BUFFER_THREADS_CHANNEL_ID = os.getenv("BUFFER_THREADS_CHANNEL_ID", "")
BUFFER_TIKTOK_CHANNEL_ID  = os.getenv("BUFFER_TIKTOK_CHANNEL_ID", "")
BUFFER_X_CHANNEL_ID       = os.getenv("BUFFER_X_CHANNEL_ID", "")

BUFFER_ENDPOINT = "https://api.buffer.com"


# ---------------------------------------------------------------------------
# Scheduling helpers
# ---------------------------------------------------------------------------

def _next_weekday_random(weekday: int, window_start_utc: int, window_end_utc: int) -> str:
    """Return an ISO 8601 UTC timestamp on the next occurrence of weekday,
    at a random minute within [window_start_utc, window_end_utc) hours UTC.
    Always the next occurrence — never today.
    Minutes are fully random so posts look organic (e.g. 10:14, 11:37)."""
    import random
    now = datetime.now(timezone.utc)
    days_ahead = weekday - now.weekday()
    if days_ahead <= 0:
        days_ahead += 7
    base = (now + timedelta(days=days_ahead)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Pick a random minute within the window
    window_minutes = (window_end_utc - window_start_utc) * 60
    offset_minutes = random.randint(0, window_minutes - 1)
    target = base + timedelta(hours=window_start_utc, minutes=offset_minutes)
    return target.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Platform-specific send times — random within optimal windows (ET audience)
#
# Mon: article publishes to Substack
# Tue: X card           →  8–10am ET = 13–15 UTC  (morning scroll)
# Wed: Instagram card   → 11am–1pm ET = 16–18 UTC  (lunch peak)
#      Threads card     → 12–2pm ET   = 17–19 UTC  (midday)
# Thu: TikTok video     →  6–9pm ET   = 23–00 UTC  (prime TikTok window)
# Fri: Instagram Reels  →  7–9pm ET   = 00–02 UTC  (Reels peak)
#      Threads video    →  6–8pm ET   = 23–01 UTC  (evening scroll)
# ---------------------------------------------------------------------------

def next_tuesday_x() -> str:
    """X card — Tuesday, random 8–10am ET (13–15 UTC)."""
    return _next_weekday_random(1, window_start_utc=13, window_end_utc=15)

def next_wednesday_instagram() -> str:
    """Instagram card — Wednesday, random 11am–1pm ET (16–18 UTC)."""
    return _next_weekday_random(2, window_start_utc=16, window_end_utc=18)

def next_wednesday_threads() -> str:
    """Threads card — Wednesday, random 12–2pm ET (17–19 UTC)."""
    return _next_weekday_random(2, window_start_utc=17, window_end_utc=19)

def next_thursday_tiktok() -> str:
    """TikTok video — Thursday, random 6–9pm ET (23–00 UTC)."""
    return _next_weekday_random(3, window_start_utc=23, window_end_utc=24)

def next_friday_instagram() -> str:
    """Instagram Reels — Friday, random 7–9pm ET (00–02 UTC Saturday)."""
    return _next_weekday_random(5, window_start_utc=0, window_end_utc=2)

def next_friday_threads() -> str:
    """Threads video — Friday, random 6–8pm ET (23–01 UTC)."""
    return _next_weekday_random(4, window_start_utc=23, window_end_utc=24)

# Legacy aliases so nothing breaks
def next_tuesday_noon() -> str:
    return next_wednesday_instagram()

def next_tuesday_instagram() -> str:
    return next_wednesday_instagram()

def next_tuesday_threads() -> str:
    return next_wednesday_threads()

def next_thursday_x() -> str:
    return next_tuesday_x()

def next_thursday_instagram() -> str:
    return next_friday_instagram()

def next_thursday_threads() -> str:
    return next_friday_threads()

def next_thursday_noon() -> str:
    return next_friday_instagram()


# ---------------------------------------------------------------------------
# Cloudinary upload (Buffer needs a public URL for media)
# ---------------------------------------------------------------------------

def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
        api_key=os.getenv("CLOUDINARY_API_KEY", ""),
        api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
    )


def _upload_media(media_path: Path, resource_type: str = "image") -> str:
    _configure_cloudinary()
    result = cloudinary.uploader.upload(
        str(media_path),
        folder="ivyedge/social",
        overwrite=False,
        resource_type=resource_type,
    )
    url = result.get("secure_url", "")
    logger.info("Uploaded to Cloudinary (%s): %s", resource_type, url)
    return url


# ---------------------------------------------------------------------------
# Buffer GraphQL helper
# ---------------------------------------------------------------------------

def _gql(query: str, variables: Optional[dict] = None) -> dict:
    if not BUFFER_API_KEY:
        raise ValueError("BUFFER_API_KEY not set in .env")
    resp = requests.post(
        BUFFER_ENDPOINT,
        json={"query": query, "variables": variables or {}},
        headers={
            "Authorization": f"Bearer {BUFFER_API_KEY}",
            "Content-Type":  "application/json",
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        logger.error("Buffer GraphQL errors: %s", data["errors"])
    return data


# ---------------------------------------------------------------------------
# Channel discovery
# ---------------------------------------------------------------------------

def list_channels() -> list[dict]:
    """Return all Buffer channels connected to the account."""
    org_id = BUFFER_ORG_ID
    if not org_id:
        # Fall back to fetching org ID dynamically
        r = _gql("{ account { organizations { id } } }")
        orgs = r.get("data", {}).get("account", {}).get("organizations", [])
        org_id = orgs[0]["id"] if orgs else ""
    result = _gql(
        'query($org: OrganizationId!) { channels(input: {organizationId: $org}) { id name service } }',
        {"org": org_id},
    )
    return result.get("data", {}).get("channels", [])


# ---------------------------------------------------------------------------
# Post creation
# ---------------------------------------------------------------------------

def _create_post(
    channel_id: str,
    text: str,
    image_url: Optional[str] = None,
    video_url: Optional[str] = None,
    platform: str = "threads",
    scheduled_at: Optional[str] = None,
) -> Optional[str]:
    """Create a Buffer post — scheduled if scheduled_at is provided, else immediate. Returns post ID or None."""
    assets_parts = []
    if image_url:
        assets_parts.append(f'images: [{{ url: {_gql_string(image_url)} }}]')
    if video_url:
        assets_parts.append(f'videos: [{{ url: {_gql_string(video_url)} }}]')
    assets_block = f"assets: {{ {' '.join(assets_parts)} }}" if assets_parts else ""

    if platform == "instagram":
        post_type = "reel" if video_url else "post"
        metadata_block = f'metadata: {{ instagram: {{ type: {post_type} shouldShareToFeed: true }} }}'
    elif platform == "tiktok":
        metadata_block = ""
    else:
        metadata_block = ""

    if scheduled_at:
        # Buffer API: mode=customScheduled + dueAt (not scheduledAt)
        schedule_block = f'mode: customScheduled\n            schedulingType: automatic\n            dueAt: "{scheduled_at}"'
    else:
        schedule_block = "mode: shareNow\n            schedulingType: automatic"

    mutation = f"""
        mutation {{
          createPost(input: {{
            text: {_gql_string(text)}
            channelId: "{channel_id}"
            {schedule_block}
            {assets_block}
            {metadata_block}
          }}) {{
            ... on PostActionSuccess {{
              post {{ id }}
            }}
            ... on MutationError {{
              message
            }}
          }}
        }}
    """
    result  = _gql(mutation)
    payload = result.get("data", {}).get("createPost", {})

    if "message" in payload:
        logger.error("Buffer post error: %s", payload["message"])
        return None

    post_id = payload.get("post", {}).get("id")
    logger.info("Buffer post created: %s (channel %s)", post_id, channel_id)
    return post_id


def _gql_string(text: str) -> str:
    """Escape a string for inline GraphQL."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _upload_media_safe(
    path: Optional[Path],
    resource_type: str,
    label: str,
) -> Optional[str]:
    if not path or not path.exists():
        return None
    try:
        return _upload_media(path, resource_type)
    except Exception as e:
        logger.warning("%s upload failed: %s", label, e)
        return None


# ---------------------------------------------------------------------------
# Platform-specific posts
# ---------------------------------------------------------------------------

def post_to_instagram(
    caption: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
    scheduled_at: Optional[str] = None,
) -> Optional[str]:
    if not BUFFER_IG_CHANNEL_ID:
        logger.error("BUFFER_IG_CHANNEL_ID not set — run --list-channels")
        return None
    video_url = _upload_media_safe(video_path, "video", "Instagram video")
    image_url = _upload_media_safe(image_path, "image", "Instagram image") if not video_url else None
    return _create_post(
        BUFFER_IG_CHANNEL_ID, caption,
        image_url=image_url, video_url=video_url,
        platform="instagram", scheduled_at=scheduled_at,
    )


def post_to_threads(
    text: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
    scheduled_at: Optional[str] = None,
) -> Optional[str]:
    if not BUFFER_THREADS_CHANNEL_ID:
        logger.error("BUFFER_THREADS_CHANNEL_ID not set — run --list-channels")
        return None
    video_url = _upload_media_safe(video_path, "video", "Threads video")
    image_url = _upload_media_safe(image_path, "image", "Threads image") if not video_url else None
    return _create_post(
        BUFFER_THREADS_CHANNEL_ID, text,
        image_url=image_url, video_url=video_url,
        platform="threads", scheduled_at=scheduled_at,
    )


def post_to_tiktok(
    text: str,
    video_path: Path,
    scheduled_at: Optional[str] = None,
) -> Optional[str]:
    if not BUFFER_TIKTOK_CHANNEL_ID:
        logger.error("BUFFER_TIKTOK_CHANNEL_ID not set — run --list-channels")
        return None
    video_url = _upload_media_safe(video_path, "video", "TikTok video")
    if not video_url:
        logger.error("TikTok video upload failed — cannot post without video")
        return None
    return _create_post(
        BUFFER_TIKTOK_CHANNEL_ID, text,
        video_url=video_url,
        platform="tiktok", scheduled_at=scheduled_at,
    )


def post_to_x(
    text: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
    scheduled_at: Optional[str] = None,
) -> Optional[str]:
    if not BUFFER_X_CHANNEL_ID:
        logger.error("BUFFER_X_CHANNEL_ID not set — run --list-channels")
        return None
    video_url = _upload_media_safe(video_path, "video", "X video")
    image_url = _upload_media_safe(image_path, "image", "X image") if not video_url else None
    return _create_post(
        BUFFER_X_CHANNEL_ID, text,
        image_url=image_url, video_url=video_url,
        platform="twitter", scheduled_at=scheduled_at,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if "--list-channels" in sys.argv:
        channels = list_channels()
        print(f"\nFound {len(channels)} Buffer channel(s):\n")
        for c in channels:
            print(f"  Service:    {c.get('service')}")
            print(f"  Username:   {c.get('username') or c.get('name')}")
            print(f"  Channel ID: {c.get('id')}")
            print()
        print("Add these to your .env:")
        print("  BUFFER_IG_CHANNEL_ID=...")
        print("  BUFFER_THREADS_CHANNEL_ID=...")
        print("  BUFFER_TIKTOK_CHANNEL_ID=...")
    else:
        print("Usage: python buffer_poster.py --list-channels")
