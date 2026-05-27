"""
Ivy Edge Meta Poster — Threads + Instagram

Posts branded content to Threads and Instagram via the Meta Graph API.

Each post gets two assets:
  - Image card (1080x1080 PNG) → Instagram feed post + Threads image post
  - Video (1080x1920 MP4)      → Instagram Reel + Threads video post

Required in .env:
  META_ACCESS_TOKEN=...     Long-lived page/user access token
  IG_USER_ID=...            Instagram Business account user ID
  THREADS_USER_ID=...       Threads user ID (same as IG user ID usually)
  CLOUDINARY_CLOUD_NAME=... For hosting media (Meta API needs a public URL)
  CLOUDINARY_API_KEY=...
  CLOUDINARY_API_SECRET=...

Setup guide: see README — one-time Meta developer app setup required.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import requests
import cloudinary
import cloudinary.uploader
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("ivyedge.meta_poster")

META_ACCESS_TOKEN  = os.getenv("META_ACCESS_TOKEN", "")
IG_USER_ID         = os.getenv("IG_USER_ID", "")
THREADS_USER_ID    = os.getenv("THREADS_USER_ID", "")

GRAPH_BASE         = "https://graph.facebook.com/v19.0"
THREADS_BASE       = "https://graph.threads.net/v1.0"


def _configure_cloudinary() -> None:
    cloudinary.config(
        cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME", ""),
        api_key=os.getenv("CLOUDINARY_API_KEY", ""),
        api_secret=os.getenv("CLOUDINARY_API_SECRET", ""),
    )


def _check_credentials(platform: str) -> bool:
    if not META_ACCESS_TOKEN:
        logger.error("META_ACCESS_TOKEN not set — cannot post to %s", platform)
        return False
    if platform == "instagram" and not IG_USER_ID:
        logger.error("IG_USER_ID not set")
        return False
    if platform == "threads" and not THREADS_USER_ID:
        logger.error("THREADS_USER_ID not set")
        return False
    return True


# ---------------------------------------------------------------------------
# Cloudinary image upload
# ---------------------------------------------------------------------------

def _upload_media(media_path: Path, resource_type: str = "image") -> str:
    """Upload image or video to Cloudinary and return the public HTTPS URL."""
    _configure_cloudinary()
    if not os.getenv("CLOUDINARY_CLOUD_NAME"):
        raise ValueError(
            "CLOUDINARY_CLOUD_NAME not set in .env.\n"
            "Sign up free at cloudinary.com and add your credentials."
        )
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
# Instagram poster
# ---------------------------------------------------------------------------

def _ig_publish(container_id: str) -> Optional[str]:
    """Publish a prepared Instagram container. Returns post URL or None."""
    publish_url = f"{GRAPH_BASE}/{IG_USER_ID}/media_publish"
    resp = requests.post(publish_url, data={
        "creation_id":  container_id,
        "access_token": META_ACCESS_TOKEN,
    }, timeout=30)
    if not resp.ok:
        logger.error("Instagram publish failed: %s", resp.text)
        return None
    post_id = resp.json().get("id", "")
    return f"https://www.instagram.com/p/{post_id}/"


def _wait_for_ig_container(container_id: str, max_wait: int = 120) -> bool:
    """Poll until the Instagram media container is ready to publish."""
    status_url = f"{GRAPH_BASE}/{container_id}"
    for _ in range(max_wait // 5):
        time.sleep(5)
        resp = requests.get(status_url, params={
            "fields": "status_code",
            "access_token": META_ACCESS_TOKEN,
        }, timeout=15)
        if resp.ok:
            status = resp.json().get("status_code", "")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                logger.error("Instagram container processing error")
                return False
    logger.error("Instagram container timed out after %ss", max_wait)
    return False


def post_to_instagram(
    caption: str,
    image_path: Path,
) -> Optional[str]:
    """Post a static image (feed post) to Instagram."""
    if not _check_credentials("instagram"):
        return None
    try:
        image_url = _upload_media(image_path, resource_type="image")
    except Exception as e:
        logger.error("Cloudinary upload failed: %s", e)
        return None

    create_url = f"{GRAPH_BASE}/{IG_USER_ID}/media"
    resp = requests.post(create_url, data={
        "image_url":    image_url,
        "caption":      caption,
        "access_token": META_ACCESS_TOKEN,
    }, timeout=30)
    if not resp.ok:
        logger.error("Instagram image container failed: %s", resp.text)
        return None

    container_id = resp.json().get("id")
    time.sleep(4)
    post_url = _ig_publish(container_id)
    if post_url:
        logger.info("Posted image to Instagram: %s", post_url)
    return post_url


def post_reel_to_instagram(
    caption: str,
    video_path: Path,
) -> Optional[str]:
    """Post a Reel (vertical video) to Instagram."""
    if not _check_credentials("instagram"):
        return None
    try:
        video_url = _upload_media(video_path, resource_type="video")
    except Exception as e:
        logger.error("Cloudinary video upload failed: %s", e)
        return None

    create_url = f"{GRAPH_BASE}/{IG_USER_ID}/media"
    resp = requests.post(create_url, data={
        "media_type":   "REELS",
        "video_url":    video_url,
        "caption":      caption,
        "share_to_feed": "true",
        "access_token": META_ACCESS_TOKEN,
    }, timeout=30)
    if not resp.ok:
        logger.error("Instagram Reel container failed: %s", resp.text)
        return None

    container_id = resp.json().get("id")
    logger.info("Instagram Reel container created: %s — waiting for processing...", container_id)

    if not _wait_for_ig_container(container_id):
        return None

    post_url = _ig_publish(container_id)
    if post_url:
        logger.info("Posted Reel to Instagram: %s", post_url)
    return post_url


# ---------------------------------------------------------------------------
# Threads poster
# ---------------------------------------------------------------------------

def _threads_publish(container_id: str) -> Optional[str]:
    """Publish a prepared Threads container. Returns post URL or None."""
    publish_url = f"{THREADS_BASE}/{THREADS_USER_ID}/threads_publish"
    resp = requests.post(publish_url, data={
        "creation_id":  container_id,
        "access_token": META_ACCESS_TOKEN,
    }, timeout=30)
    if not resp.ok:
        logger.error("Threads publish failed: %s", resp.text)
        return None
    post_id = resp.json().get("id", "")
    return f"https://www.threads.net/t/{post_id}"


def post_to_threads(
    text: str,
    image_path: Optional[Path] = None,
    video_path: Optional[Path] = None,
) -> Optional[str]:
    """
    Post to Threads. Prefers video over image if both are provided.
    Falls back to text-only if neither upload succeeds.
    """
    if not _check_credentials("threads"):
        return None

    create_url = f"{THREADS_BASE}/{THREADS_USER_ID}/threads"
    payload: dict = {"text": text, "access_token": META_ACCESS_TOKEN}

    # Video takes priority
    if video_path and video_path.exists():
        try:
            video_url = _upload_media(video_path, resource_type="video")
            payload["media_type"] = "VIDEO"
            payload["video_url"]  = video_url
        except Exception as e:
            logger.warning("Video upload failed for Threads, trying image: %s", e)

    if not payload.get("media_type") and image_path and image_path.exists():
        try:
            image_url = _upload_media(image_path, resource_type="image")
            payload["media_type"] = "IMAGE"
            payload["image_url"]  = image_url
        except Exception as e:
            logger.warning("Image upload failed, posting text-only to Threads: %s", e)

    if "media_type" not in payload:
        payload["media_type"] = "TEXT"

    resp = requests.post(create_url, data=payload, timeout=30)
    if not resp.ok:
        logger.error("Threads container creation failed: %s", resp.text)
        return None

    container_id = resp.json().get("id")
    logger.info("Threads container created: %s", container_id)
    time.sleep(4)

    post_url = _threads_publish(container_id)
    if post_url:
        logger.info("Posted to Threads: %s", post_url)
    return post_url
