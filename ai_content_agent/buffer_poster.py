"""
Ivy Edge Buffer Poster — Instagram, Threads, TikTok

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
# Tue: X post (8–10am ET)  |  Cat TikTok 1 (7–9pm ET)
# Wed: Cat IG 1 (10am–12pm ET)  |  Threads (12–2pm ET)
# Thu: Cat TikTok 3 (7–9pm ET)
# Fri: Cat IG 2 (3–5pm ET)
# Sat: Cat TikTok 2 (9–11am ET)
# ---------------------------------------------------------------------------

# ── X (Twitter) ──────────────────────────────────────────────────────────

def next_tuesday_x() -> str:
    """X post 1 — Tuesday, random 8–10am ET (13–15 UTC)."""
    return _next_weekday_random(1, window_start_utc=13, window_end_utc=15)

def next_wednesday_x() -> str:
    """X post 2 — Wednesday, random 11am–1pm ET (16–18 UTC)."""
    return _next_weekday_random(2, window_start_utc=16, window_end_utc=18)

def next_thursday_x() -> str:
    """X post 3 — Thursday, random 8–10am ET (13–15 UTC)."""
    return _next_weekday_random(3, window_start_utc=13, window_end_utc=15)

# ── Instagram feed ────────────────────────────────────────────────────────

def next_tuesday_ig() -> str:
    """IG feed post 1 — Tuesday, random 11am–1pm ET (16–18 UTC)."""
    return _next_weekday_random(1, window_start_utc=16, window_end_utc=18)

def next_thursday_ig() -> str:
    """IG feed post 2 — Thursday, random 11am–1pm ET (16–18 UTC)."""
    return _next_weekday_random(3, window_start_utc=16, window_end_utc=18)

def next_saturday_ig() -> str:
    """IG feed post 3 — Saturday, random 10am–12pm ET (15–17 UTC)."""
    return _next_weekday_random(5, window_start_utc=15, window_end_utc=17)

# ── TikTok / Reels ────────────────────────────────────────────────────────

def next_tuesday_cat_tiktok() -> str:
    """TikTok video 1 (cat) — Tuesday, random 7–9pm ET (23–25 UTC). Evening — clears the morning X post."""
    return _next_weekday_random(1, window_start_utc=23, window_end_utc=25)

def next_wednesday_cat_ig() -> str:
    """Cat IG photo 1 — Wednesday, random 10am–12pm ET (14–16 UTC). Morning — before Threads at noon."""
    return _next_weekday_random(2, window_start_utc=14, window_end_utc=16)

def next_saturday_tiktok() -> str:
    """TikTok video 2 — Saturday, random 9–11am ET (13–15 UTC). Morning slot — weekend scroll."""
    return _next_weekday_random(5, window_start_utc=13, window_end_utc=15)

def next_thursday_cat_tiktok() -> str:
    """TikTok video 3 (cat) — Thursday, random 7–9pm ET (23–25 UTC). Evening slot — pre-weekend wind-down."""
    return _next_weekday_random(3, window_start_utc=23, window_end_utc=25)


def next_friday_cat_ig() -> str:
    """Cat IG photo 2 — Friday, random 3–5pm ET (19–21 UTC)."""
    return _next_weekday_random(4, window_start_utc=19, window_end_utc=21)

# ── Threads ───────────────────────────────────────────────────────────────

def next_wednesday_threads() -> str:
    """Threads — Wednesday, random 12–2pm ET (17–19 UTC)."""
    return _next_weekday_random(2, window_start_utc=17, window_end_utc=19)

# ── Stories ───────────────────────────────────────────────────────────────

def next_tuesday_story() -> str:
    """Story 1 — Tuesday, random 7–9am ET (12–14 UTC)."""
    return _next_weekday_random(1, window_start_utc=12, window_end_utc=14)

def next_wednesday_story() -> str:
    """Story 2 — Wednesday, random 8–10am ET (13–15 UTC)."""
    return _next_weekday_random(2, window_start_utc=13, window_end_utc=15)

def next_thursday_story() -> str:
    """Story 3 — Thursday, random 7–9am ET (12–14 UTC)."""
    return _next_weekday_random(3, window_start_utc=12, window_end_utc=14)

def next_friday_story() -> str:
    """Story 4 — Friday, random 8–10am ET (13–15 UTC)."""
    return _next_weekday_random(4, window_start_utc=13, window_end_utc=15)

# ── Legacy aliases (keep old call-sites working) ──────────────────────────

def next_tuesday_noon() -> str:
    return next_tuesday_ig()

def next_tuesday_instagram() -> str:
    return next_tuesday_ig()

def next_tuesday_threads() -> str:
    return next_wednesday_threads()

def next_wednesday_instagram() -> str:
    return next_tuesday_ig()

def next_thursday_tiktok() -> str:
    return next_wednesday_tiktok()

def next_thursday_instagram() -> str:
    return next_thursday_ig()

def next_thursday_threads() -> str:
    return next_wednesday_threads()

def next_thursday_noon() -> str:
    return next_thursday_ig()

def next_friday_instagram() -> str:
    return next_saturday_ig()

def next_friday_threads() -> str:
    return next_wednesday_threads()


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
    # Buffer API: AssetInput now uses singular `image`/`video` objects, not arrays
    assets_parts = []
    if image_url:
        assets_parts.append(f'image: {{ url: {_gql_string(image_url)} }}')
    if video_url:
        assets_parts.append(f'video: {{ url: {_gql_string(video_url)} }}')
    assets_block = f"assets: {{ {' '.join(assets_parts)} }}" if assets_parts else ""

    if platform == "instagram_story":
        metadata_block = 'metadata: { instagram: { type: story } }'
    elif platform == "instagram":
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


def post_instagram_story(
    image_path: Path,
    caption: str = "",
    scheduled_at: Optional[str] = None,
) -> Optional[str]:
    """Post an image as an Instagram Story via Buffer."""
    if not BUFFER_IG_CHANNEL_ID:
        logger.error("BUFFER_IG_CHANNEL_ID not set — run --list-channels")
        return None
    image_url = _upload_media_safe(image_path, "image", "Story image")
    if not image_url:
        logger.error("Story image upload failed")
        return None
    return _create_post(
        BUFFER_IG_CHANNEL_ID, caption,
        image_url=image_url,
        platform="instagram_story", scheduled_at=scheduled_at,
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
# Cat content placeholder scheduling
# ---------------------------------------------------------------------------

def _parse_cat_slots(brief_md: str) -> list[dict]:
    """Parse the cat brief markdown and return a list of slot dicts.

    Each dict has: type ('video'|'photo'), number (1-3/1-2), title, caption.
    """
    import re
    slots: list[dict] = []

    # Videos — ### Video N: Title
    for m in re.finditer(
        r"###\s*Video\s*(\d+):\s*(.+?)\n.*?(?:\*\*Caption starter:\*\*\s*\n?)(.*?)(?=\n###|\n---|\n##|\Z)",
        brief_md, re.DOTALL
    ):
        num, title, caption = m.group(1), m.group(2).strip(), m.group(3).strip()
        slots.append({"type": "video", "number": int(num), "title": title, "caption": caption})

    # Photos — ### Photo N: Title
    for m in re.finditer(
        r"###\s*Photo\s*(\d+):\s*(.+?)\n.*?(?:\*\*Caption:\*\*\s*\n?)(.*?)(?=\n###|\n---|\n##|\Z)",
        brief_md, re.DOTALL
    ):
        num, title, caption = m.group(1), m.group(2).strip(), m.group(3).strip()
        slots.append({"type": "photo", "number": int(num), "title": title, "caption": caption})

    # Sort: photo 1, video 1, video 2, photo 2, video 3 (posting order)
    order = {"photo_1": 0, "video_1": 1, "video_2": 2, "photo_2": 3, "video_3": 4}
    slots.sort(key=lambda s: order.get(f"{s['type']}_{s['number']}", 99))
    return slots


def schedule_cat_content_slots(brief_md: str, cat_name: str = "Babs") -> dict:
    """Schedule all 5 cat content slots in Buffer with a placeholder image/video.

    Buffer's API requires media for Instagram and TikTok — we upload the Ivy Edge
    logo as a placeholder. The girls replace it with their real photo/video in
    Buffer before each post goes live.

    Each post caption starts with a clear identifier so they know which slot is which.

    Slot schedule:
      Video 1  → Tuesday   7–9pm ET   (TikTok — after morning X post)
      Photo 1  → Wednesday 10am–12pm ET (Instagram — before Threads at noon)
      Video 3  → Thursday  7–9pm ET   (TikTok)
      Photo 2  → Friday    3–5pm ET   (Instagram)
      Video 2  → Saturday  9–11am ET  (TikTok)

    Returns a dict of slot keys → Buffer post IDs (or None on failure).
    """
    slots = _parse_cat_slots(brief_md)

    schedule_map = {
        ("photo", 1): next_wednesday_cat_ig,
        ("video", 1): next_tuesday_cat_tiktok,
        ("video", 2): next_saturday_tiktok,
        ("photo", 2): next_friday_cat_ig,
        ("video", 3): next_thursday_cat_tiktok,
    }

    # Upload placeholder images once — reused across slots.
    # TikTok max pixel count is 2,073,600 so we use a smaller resized version.
    assets = Path(__file__).parent / "assets" / "logos"
    placeholder_ig_url:  Optional[str] = None
    placeholder_tok_url: Optional[str] = None
    try:
        placeholder_ig_url = _upload_media(assets / "full_logo.png", resource_type="image")
        logger.info("IG placeholder uploaded: %s", placeholder_ig_url)
    except Exception as e:
        logger.warning("IG placeholder upload failed: %s", e)
    try:
        placeholder_tok_url = _upload_media(assets / "placeholder_tiktok.png", resource_type="image")
        logger.info("TikTok placeholder uploaded: %s", placeholder_tok_url)
    except Exception as e:
        logger.warning("TikTok placeholder upload failed: %s", e)

    results: dict = {}

    for slot in slots:
        t, n = slot["type"], slot["number"]
        key = f"{t}_{n}"
        time_fn = schedule_map.get((t, n))
        if not time_fn:
            continue

        icon    = "📹" if t == "video" else "📸"
        label   = f"{t.upper()} {n}"
        channel = BUFFER_IG_CHANNEL_ID if t == "photo" else BUFFER_TIKTOK_CHANNEL_ID

        if not channel:
            logger.warning("No Buffer channel ID for %s — skipping", key)
            results[key] = None
            continue

        identifier = (
            f"{icon} {label}: \"{slot['title']}\"\n"
            f"↑ Replace placeholder with your {t} before this goes live.\n\n"
        )
        full_caption = identifier + slot["caption"]

        try:
            # Upload a fresh copy per slot (unique public_id) so Buffer's
            # duplicate-detection doesn't block slots with the same image.
            import random, string
            uid = "".join(random.choices(string.ascii_lowercase, k=6))
            base_path = assets / ("placeholder_tiktok.png" if t == "video" else "full_logo.png")
            try:
                _configure_cloudinary()
                import cloudinary.uploader as _cu
                r = _cu.upload(
                    str(base_path),
                    public_id=f"ivyedge/placeholders/slot_{t}_{n}_{uid}",
                    resource_type="image",
                )
                placeholder_url = r["secure_url"]
            except Exception as _e:
                logger.warning("Per-slot placeholder upload failed, falling back: %s", _e)
                placeholder_url = placeholder_tok_url if t == "video" else placeholder_ig_url

            post_id = _create_post(
                channel_id=channel,
                text=full_caption,
                image_url=placeholder_url,  # required by Buffer API; girls replace before posting
                platform="tiktok" if t == "video" else "instagram",
                scheduled_at=time_fn(),
            )
            results[key] = post_id
            logger.info("Cat slot %s scheduled → Buffer post %s", key, post_id)
        except Exception as e:
            logger.error("Cat slot %s failed: %s", key, e)
            results[key] = None

    return results


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
