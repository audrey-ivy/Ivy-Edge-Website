"""
IvyEdge Buffer Poster — Instagram + Threads via Buffer API

Posts image cards and captions to Instagram and Threads through Buffer,
which handles the Meta authentication so no Facebook account is needed.

Required in .env:
  BUFFER_ACCESS_TOKEN=...         From buffer.com/developers/apps
  BUFFER_IG_PROFILE_ID=...        Instagram profile ID from Buffer
  BUFFER_THREADS_PROFILE_ID=...   Threads profile ID from Buffer

Run `python buffer_poster.py --list-profiles` after adding your token
to find your profile IDs automatically.
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

BUFFER_ACCESS_TOKEN       = os.getenv("BUFFER_ACCESS_TOKEN", "")
BUFFER_IG_PROFILE_ID      = os.getenv("BUFFER_IG_PROFILE_ID", "")
BUFFER_THREADS_PROFILE_ID = os.getenv("BUFFER_THREADS_PROFILE_ID", "")

BUFFER_API = "https://api.bufferapp.com/1"


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
# Profile discovery
# ---------------------------------------------------------------------------

def list_profiles() -> list[dict]:
    """Return all Buffer profiles connected to the account."""
    if not BUFFER_ACCESS_TOKEN:
        raise ValueError("BUFFER_ACCESS_TOKEN not set in .env")
    resp = requests.get(
        f"{BUFFER_API}/profiles.json",
        params={"access_token": BUFFER_ACCESS_TOKEN},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Post helper
# ---------------------------------------------------------------------------

def _post(profile_id: str, text: str, media_url: Optional[str] = None,
          media_type: str = "image") -> Optional[str]:
    """Create and immediately publish a Buffer post."""
    if not BUFFER_ACCESS_TOKEN:
        logger.error("BUFFER_ACCESS_TOKEN not set")
        return None

    data: dict = {
        "access_token":   BUFFER_ACCESS_TOKEN,
        "profile_ids[]":  profile_id,
        "text":           text,
        "now":            "true",
    }
    if media_url and media_type == "image":
        data["media[photo]"] = media_url
    elif media_url and media_type == "video":
        data["media[video]"] = media_url

    resp = requests.post(f"{BUFFER_API}/updates/create.json", data=data, timeout=30)
    if not resp.ok:
        logger.error("Buffer post failed: %s", resp.text)
        return None

    result  = resp.json()
    updates = result.get("updates", [{}])
    post_id = updates[0].get("id", "") if updates else ""
    logger.info("Buffer post created: %s", post_id)
    return post_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def post_to_instagram(
    caption: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
) -> Optional[str]:
    """Post to Instagram via Buffer. Prefers video (Reel) over image."""
    if not BUFFER_IG_PROFILE_ID:
        logger.error("BUFFER_IG_PROFILE_ID not set in .env")
        return None

    media_url  = None
    media_type = "image"

    if video_path and video_path.exists():
        try:
            media_url  = _upload_media(video_path, resource_type="video")
            media_type = "video"
        except Exception as e:
            logger.warning("Video upload failed, trying image: %s", e)

    if not media_url and image_path and image_path.exists():
        try:
            media_url  = _upload_media(image_path, resource_type="image")
            media_type = "image"
        except Exception as e:
            logger.warning("Image upload failed, posting text-only: %s", e)

    post_id = _post(BUFFER_IG_PROFILE_ID, caption, media_url, media_type)
    return f"https://www.instagram.com/ (Buffer post id: {post_id})" if post_id else None


def post_to_threads(
    text: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
) -> Optional[str]:
    """Post to Threads via Buffer. Prefers video over image."""
    if not BUFFER_THREADS_PROFILE_ID:
        logger.error("BUFFER_THREADS_PROFILE_ID not set in .env")
        return None

    media_url  = None
    media_type = "image"

    if video_path and video_path.exists():
        try:
            media_url  = _upload_media(video_path, resource_type="video")
            media_type = "video"
        except Exception as e:
            logger.warning("Video upload failed, trying image: %s", e)

    if not media_url and image_path and image_path.exists():
        try:
            media_url  = _upload_media(image_path, resource_type="image")
            media_type = "image"
        except Exception as e:
            logger.warning("Image upload failed, posting text-only: %s", e)

    post_id = _post(BUFFER_THREADS_PROFILE_ID, text, media_url, media_type)
    return f"https://www.threads.net/ (Buffer post id: {post_id})" if post_id else None


# ---------------------------------------------------------------------------
# CLI — list profiles to find IDs
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if "--list-profiles" in sys.argv:
        profiles = list_profiles()
        print(f"\nFound {len(profiles)} Buffer profile(s):\n")
        for p in profiles:
            print(f"  Service:    {p.get('service')}")
            print(f"  Username:   {p.get('formatted_username') or p.get('username')}")
            print(f"  Profile ID: {p.get('id')}")
            print()
        print("Add the IDs above to your .env as BUFFER_IG_PROFILE_ID and BUFFER_THREADS_PROFILE_ID")
    else:
        print("Usage: python buffer_poster.py --list-profiles")
