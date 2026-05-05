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
from pathlib import Path
from typing import Optional

import requests
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("ivyedge.buffer")

BUFFER_API_KEY            = os.getenv("BUFFER_API_KEY", "")
BUFFER_ORG_ID             = os.getenv("BUFFER_ORG_ID", "")
BUFFER_IG_CHANNEL_ID      = os.getenv("BUFFER_IG_CHANNEL_ID", "")
BUFFER_THREADS_CHANNEL_ID = os.getenv("BUFFER_THREADS_CHANNEL_ID", "")
BUFFER_TIKTOK_CHANNEL_ID  = os.getenv("BUFFER_TIKTOK_CHANNEL_ID", "")

BUFFER_ENDPOINT = "https://api.buffer.com"


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

def _create_post(channel_id: str, text: str, media_url: Optional[str] = None) -> Optional[str]:
    """Create and immediately queue a Buffer post. Returns post ID or None."""
    media_part = f'mediaUrls: ["{media_url}"]' if media_url else ""
    mutation = f"""
        mutation {{
          createPost(input: {{
            text: {_gql_string(text)}
            channelId: "{channel_id}"
            schedulingType: immediate
            {media_part}
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


def _get_media_url(
    video_path: Optional[Path],
    image_path: Optional[Path],
) -> Optional[str]:
    if video_path and video_path.exists():
        try:
            return _upload_media(video_path, "video")
        except Exception as e:
            logger.warning("Video upload failed, trying image: %s", e)
    if image_path and image_path.exists():
        try:
            return _upload_media(image_path, "image")
        except Exception as e:
            logger.warning("Image upload failed: %s", e)
    return None


# ---------------------------------------------------------------------------
# Platform-specific posts
# ---------------------------------------------------------------------------

def post_to_instagram(
    caption: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
) -> Optional[str]:
    if not BUFFER_IG_CHANNEL_ID:
        logger.error("BUFFER_IG_CHANNEL_ID not set — run --list-channels")
        return None
    media_url = _get_media_url(video_path, image_path)
    return _create_post(BUFFER_IG_CHANNEL_ID, caption, media_url)


def post_to_threads(
    text: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
) -> Optional[str]:
    if not BUFFER_THREADS_CHANNEL_ID:
        logger.error("BUFFER_THREADS_CHANNEL_ID not set — run --list-channels")
        return None
    media_url = _get_media_url(video_path, image_path)
    return _create_post(BUFFER_THREADS_CHANNEL_ID, text, media_url)


def post_to_tiktok(
    text: str,
    video_path: Path,
) -> Optional[str]:
    if not BUFFER_TIKTOK_CHANNEL_ID:
        logger.error("BUFFER_TIKTOK_CHANNEL_ID not set — run --list-channels")
        return None
    if not video_path.exists():
        logger.error("TikTok video not found: %s", video_path)
        return None
    try:
        video_url = _upload_media(video_path, "video")
    except Exception as e:
        logger.error("TikTok video upload failed: %s", e)
        return None
    return _create_post(BUFFER_TIKTOK_CHANNEL_ID, text, video_url)


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
