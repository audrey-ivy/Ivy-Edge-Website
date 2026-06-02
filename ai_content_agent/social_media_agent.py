"""
Ivy Edge Social Media Agent

Scans the output/ directory for posts that have been generated but not yet
posted to social media. For each one:

  1. Generates a branded 1080x1080 image card (Instagram / Threads)
  2. Generates a TikTok/Reels MP4 (ivy background + ElevenLabs voiceover)
  3. Posts the image + caption to Instagram
  4. Posts the image + text to Threads
  5. Saves a social_posted.json receipt so the post is never double-posted

Called automatically from run_monday.sh after the content pipeline runs.
Can also be run manually:

    python social_media_agent.py                    # process all unpublished
    python social_media_agent.py --folder output/2026-05-06_why-your-career-gap...
    python social_media_agent.py --cards-only       # generate cards, skip posting
    python social_media_agent.py --video-only       # generate videos only
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv(override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ivyedge.social")

OUTPUT_DIR   = Path(__file__).parent / "output"
CALENDAR_CSV = Path(__file__).parent / "editorial_calendar.csv"


# ---------------------------------------------------------------------------
# Live Substack published check
# ---------------------------------------------------------------------------

def _live_substack_posts() -> dict[str, dict]:
    """Fetch all currently-published posts from Substack.
    Returns a dict keyed by BOTH slug and string post-id for flexible matching.
    Source of truth — an article must be is_published=True to get social posts."""
    try:
        from substack_publisher import SubstackPublisher
        pub = SubstackPublisher()
        posts_by_key: dict[str, dict] = {}
        cursor = None
        while True:
            url = "https://joinivyedge.substack.com/api/v1/drafts?filter=published&limit=20"
            if cursor:
                url += f"&cursor={cursor}"
            resp = pub.session.get(url, timeout=15)
            if not resp.ok:
                logger.error("Substack API returned %s — cannot verify published status", resp.status_code)
                return {}
            data  = resp.json()
            posts = data.get("posts", [])
            for p in posts:
                if not p.get("is_published"):
                    continue
                post_id = str(p.get("id", ""))
                slug    = p.get("slug", "")
                if post_id:
                    posts_by_key[post_id] = p   # match by numeric ID
                if slug:
                    posts_by_key[slug] = p       # match by slug
            if not data.get("hasMore"):
                break
            cursor = data.get("nextCursor")
        live_count = len({v["id"] for v in posts_by_key.values()})
        logger.info("Substack live check: %d published article(s) found", live_count)
        return posts_by_key
    except Exception as e:
        logger.error("Could not reach Substack API to verify published status: %s", e)
        return {}


# Keep this name so callers that just need the key-set still work
def _live_substack_slugs() -> set[str]:
    return set(_live_substack_posts().keys())


def _is_published(folder: Path, live_slugs: Optional[set[str]] = None) -> bool:
    """Return True only if this folder's article is currently live on Substack.

    Matching priority:
      1. Numeric post ID from substack_url.txt  (most reliable)
      2. Slug from substack_url.txt
      3. Folder name prefix vs live slugs       (fallback)
    """
    if live_slugs is None:
        live_slugs = _live_substack_slugs()

    if not live_slugs:
        return False  # API unreachable — fail safe, don't post

    # 1. Read substack_url.txt — extract whatever is after the last "/"
    #    Could be a numeric ID (e.g. 197220304) or a real slug.
    #    If the file exists, it is authoritative — no fallback to name prefix.
    url_file = folder / "substack_url.txt"
    if url_file.exists():
        stored = url_file.read_text(encoding="utf-8").strip().rstrip("/")
        token = stored.split("/")[-1]   # numeric ID or slug
        return token in live_slugs      # True only if this exact ID/slug is live

    # 2. No substack_url.txt — derive slug from folder name and prefix-match.
    #    Only reaches here for folders that were never published to Substack.
    folder_slug = folder.name[11:] if len(folder.name) > 11 else folder.name
    for live_key in live_slugs:
        if (live_key.startswith(folder_slug[:35])
                or folder_slug.startswith(live_key[:35])):
            return True

    return False


# ---------------------------------------------------------------------------
# Social copy parsers
# ---------------------------------------------------------------------------

def _parse_instagram_caption(social_md: str) -> str:
    """Extract the first Instagram caption (legacy single-caption fallback)."""
    captions = _parse_instagram_captions(social_md)
    return captions[0] if captions else social_md[:2200].strip()


def _parse_instagram_captions(social_md: str) -> list[str]:
    """Extract up to 3 Instagram captions with their hashtags merged in."""
    captions: list[str] = []
    for i in range(1, 4):
        cap_match = re.search(
            rf"###\s*Caption {i}\s*\n(.*?)(?=\n###|\n##|\n---|\Z)",
            social_md, re.DOTALL
        )
        if not cap_match:
            break
        text = cap_match.group(1).strip()
        # Merge hashtags block for this caption
        htag_match = re.search(
            rf"###\s*Hashtags {i}\s*\n(.*?)(?=\n###|\n##|\n---|\Z)",
            social_md, re.DOTALL
        )
        if htag_match:
            text = text + "\n\n" + htag_match.group(1).strip()
        # Ensure blank line before hashtag block
        text = re.sub(r'(?<!\n)\n(#\w)', r'\n\n\1', text)
        captions.append(text)

    # Legacy: single ### Caption block
    if not captions:
        match = re.search(
            r"###\s*Caption\s*\n(.*?)(?=\n###|\n---|\Z)",
            social_md, re.DOTALL
        )
        if match:
            caption = match.group(1).strip()
            caption = re.sub(r'(?<!\n)\n(#\w)', r'\n\n\1', caption)
            captions.append(caption)

    return captions


def _parse_threads_post(social_md: str) -> str:
    """Extract the Threads post from 06_social.md.
    Looks for dedicated ## Threads section first; falls back to ## X / Threads Option 1."""
    # New format: dedicated ## Threads section
    match = re.search(
        r"##\s*Threads\s*\n###\s*Post\s*\n(.*?)(?=\n##|\n---|\Z)",
        social_md, re.DOTALL
    )
    if match:
        return match.group(1).strip()
    # Legacy format: ## X / Threads with options — use Option 1
    match = re.search(
        r"###\s*Option 1\s*\n(.*?)(?=\n###\s*Option|\n---|\Z)",
        social_md, re.DOTALL
    )
    if match:
        return match.group(1).strip()
    return ""


def _parse_x_posts(social_md: str, blog_url: str = "") -> list[str]:
    """Extract up to 3 X posts, each formatted and trimmed to ≤280 chars."""
    posts: list[str] = []
    for i in range(1, 4):
        match = re.search(
            rf"###\s*Post {i}\s*\n(.*?)(?=\n###\s*Post|\n###\s*(?!Post)|\n##|\n---|\Z)",
            social_md, re.DOTALL
        )
        if not match:
            break
        raw = match.group(1).strip()
        posts.append(_parse_x_post(social_md, raw_text=raw, blog_url=blog_url))

    # Legacy fallback
    if not posts:
        posts.append(_parse_x_post(social_md, blog_url=blog_url))
    return posts


def _parse_story_slides(social_md: str) -> list[str]:
    """Extract 4 story slide texts."""
    slides: list[str] = []
    for i in range(1, 5):
        match = re.search(
            rf"###\s*Slide {i}[^\n]*\n(.*?)(?=\n###|\n##|\n---|\Z)",
            social_md, re.DOTALL
        )
        if match:
            slides.append(match.group(1).strip())
    return slides


def _parse_x_post(social_md: str, fallback_threads: str = "", max_chars: int = 280,
                  blog_url: str = "", raw_text: str = "") -> str:
    """Return a ≤280-char post for X, always ending with the blog URL.
    If raw_text is provided, uses that directly. Otherwise looks for ## X section."""
    link = blog_url.strip() if blog_url.strip() else "https://www.ivyedge.co"
    # X counts every URL as 23 chars regardless of actual length
    url_chars = 23
    suffix = f"\n\n{link}"
    body_limit = max_chars - url_chars - 2  # 2 for the \n\n

    if raw_text:
        text = raw_text
    else:
        # New format: dedicated ## X section with single ### Post
        match = re.search(
            r"##\s*X\s*\n###\s*Post\s*\n(.*?)(?=\n##|\n---|\Z)",
            social_md, re.DOTALL
        )
        if match:
            text = match.group(1).strip()
        else:
            text = fallback_threads or ""

    if not text:
        return link

    # Strip hashtag lines and URLs — the link is appended automatically
    URL_RE = re.compile(r'https?://\S+|www\.\S+')
    lines = [l for l in text.splitlines()
             if not l.strip().startswith("#") and not URL_RE.fullmatch(l.strip())]
    text = "\n".join(lines).strip()

    # Ensure blank lines between non-empty lines so Buffer renders line breaks correctly.
    # Single \n is collapsed by most platforms; \n\n creates a visible break.
    text = re.sub(r'(?<!\n)\n(?!\n)(?=\S)', '\n\n', text)

    # Trim to body_limit if needed
    if len(text) > body_limit:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        result = ""
        for s in sentences:
            candidate = (result + " " + s).strip() if result else s
            if len(candidate) <= body_limit:
                result = candidate
            else:
                break
        if result and len(result) < len(text):
            result = result.rstrip(".,;") + "…"
        text = result[:body_limit] if result else text[:body_limit]

    return f"{text}{suffix}"


def _parse_reddit_post(social_md: str) -> tuple[str, str]:
    """Extract Reddit title and body from 06_social.md. Returns (title, body)."""
    title_match = re.search(r"###\s*Reddit Title\s*\n(.*?)(?=\n###|\n---|\Z)", social_md, re.DOTALL)
    body_match  = re.search(r"###\s*Reddit Body\s*\n(.*?)(?=\n###|\n---|\Z)", social_md, re.DOTALL)
    title = title_match.group(1).strip() if title_match else ""
    body  = body_match.group(1).strip()  if body_match  else ""
    return title, body


def _format_tiktok_caption(raw: str, blog_url: str = "") -> str:
    """
    Clean up TikTok caption text:
    - Ensure each sentence starts on its own line
    - Separate hashtag block with a blank line
    - Ensure blog URL is present before hashtags
    """
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    body_lines: list[str] = []
    tag_lines: list[str]  = []
    for line in lines:
        if line.startswith("#"):
            tag_lines.append(line)
        else:
            body_lines.append(line)

    # Ensure blog URL appears in body
    blog_display = blog_url.replace("https://www.", "").replace("https://", "") if blog_url else "ivyedge.co"
    has_link = any(blog_display in l or "ivyedge.co" in l for l in body_lines)
    if not has_link:
        body_lines.append(f"\n🔗 {blog_display}")

    parts = "\n\n".join(body_lines)
    if tag_lines:
        parts += "\n\n" + " ".join(tag_lines)
    return parts


def _parse_pull_quote(social_md: str) -> str:
    """Pull a short stat or hook from the Instagram caption for the image card."""
    caption = _parse_instagram_caption(social_md)
    # Grab the first sentence that looks like a stat or bold claim
    sentences = re.split(r'(?<=[.!?])\s+', caption)
    for s in sentences:
        if any(char.isdigit() for char in s) or len(s) < 120:
            return s.strip().lstrip('"').rstrip('"')
    return sentences[0].strip() if sentences else ""


# ---------------------------------------------------------------------------
# Per-folder processor
# ---------------------------------------------------------------------------

def process_folder(
    folder: Path,
    cards_only: bool = False,
    video_only: bool = False,
    skip_post: bool = False,
    live_slugs: Optional[set[str]] = None,
) -> dict:
    """Process a single output folder. Returns a result dict."""
    receipt_path = folder / "social_posted.json"
    if receipt_path.exists():
        logger.info("Already posted — skipping: %s", folder.name)
        return {"status": "already_posted", "folder": str(folder)}

    # Guard: never post social for an article that isn't live on Substack
    # live_slugs=None causes _is_published to do a fresh API fetch (used when
    # called directly via --folder or --url, not via process_all)
    if not _is_published(folder, live_slugs=live_slugs):
        logger.warning(
            "Article not live on Substack — skipping social for: %s",
            folder.name,
        )
        return {"status": "not_published", "folder": str(folder)}

    meta_path   = folder / "meta.json"
    social_path = folder / "06_social.md"

    if not social_path.exists():
        logger.warning("No 06_social.md in %s — skipping", folder.name)
        return {"status": "no_social_file"}

    meta: dict = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

    title    = meta.get("topic", folder.name)
    pillar   = meta.get("pillar", "Pillar 1: Financial Education for Non-Traditional Paths")
    # Use the canonical blog article URL — read from blog_url.txt if available
    blog_url_file = folder / "blog_url.txt"
    if blog_url_file.exists():
        blog_url = blog_url_file.read_text(encoding="utf-8").strip()
    else:
        import re as _re
        slug = _re.sub(r"[^a-z0-9\s-]", "", title.lower())
        slug = _re.sub(r"\s+", "-", slug).strip("-")
        blog_url = f"https://ivyedge.co/blog/{slug}"
    social_text = social_path.read_text(encoding="utf-8")

    result: dict = {
        "folder":     str(folder),
        "title":      title,
        "blog_url":   blog_url,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }

    # ── 1. Image cards (1080×1080) — up to 3 ───────────────────────────
    card_paths: list[Optional[Path]] = [None, None, None]
    if not video_only:
        try:
            from image_card_generator import generate_card
            pull_quote = _parse_pull_quote(social_text)
            for i in range(3):
                card_path = folder / f"07_image_card_{i+1}.png"
                generate_card(
                    title=title,
                    pillar=pillar,
                    pull_quote=pull_quote,
                    output_path=card_path,
                    dark=(i % 2 == 0),   # alternate dark/light
                    blog_url=blog_url,
                )
                card_paths[i] = card_path
                result[f"image_card_{i+1}"] = str(card_path)
            logger.info("Image cards generated: 3")
        except Exception as e:
            logger.error("Image card generation failed for %s: %s", folder.name, e)

    # ── 2. Story cards (1080×1920) — 4 slides ──────────────────────────
    story_paths: list[Optional[Path]] = [None, None, None, None]
    story_types = ["stat", "quote", "cta", "question"]
    if not video_only:
        try:
            from image_card_generator import generate_story_card
            slides = _parse_story_slides(social_text)
            for i, (slide_type, slide_text) in enumerate(zip(story_types, slides)):
                if not slide_text:
                    continue
                sp = folder / f"07_story_{i+1}.png"
                generate_story_card(
                    text=slide_text,
                    slide_type=slide_type,
                    output_path=sp,
                    blog_url=blog_url,
                )
                story_paths[i] = sp
                result[f"story_card_{i+1}"] = str(sp)
            logger.info("Story cards generated: %d", sum(1 for p in story_paths if p))
        except Exception as e:
            logger.error("Story card generation failed for %s: %s", folder.name, e)

    # ── 3. Videos — handled by Barbie (manual filming) ─────────────────
    # TikTok/Reels are filmed by the girls with Barbie each week.
    # The pipeline no longer auto-generates videos.
    video_paths: list[Optional[Path]] = [None, None]

    # ── 4. Schedule to Buffer across the week ───────────────────────────
    #
    # Automated:
    # Tue:  X post 1 (8–10am ET) | IG card 1 (11am–1pm ET) | Story 1 (7–9am ET)
    # Wed:  X post 2 (11am–1pm ET) | Threads (12–2pm ET)   | Story 2 (8–10am ET)
    # Thu:  IG card 2 (11am–1pm ET) | X post 3 (8–10am ET) | Story 3 (7–9am ET)
    # Fri:  Story 4 (8–10am ET)
    # Sat:  IG card 3 (10am–12pm ET)
    # AI-generated: X (Tue), Threads (Wed)
    # Cat content (manual upload — see 10_cat_brief.md):
    # Tue 3–5pm:   IG photo 1
    # Wed 6–9pm:   TikTok/Reel video 1
    # Fri 6–9pm:   TikTok/Reel video 2
    # Sat 3–5pm:   IG photo 2
    # Sun 6–9pm:   TikTok/Reel video 3
    if not cards_only and not video_only and not skip_post:
        from buffer_poster import (
            next_tuesday_x,
            next_wednesday_threads,
            post_to_threads, post_to_x,
        )

        x_posts      = _parse_x_posts(social_text, blog_url=blog_url)
        _threads_raw = _parse_threads_post(social_text)
        threads_text = f"{_threads_raw}\n\n{blog_url}" if _threads_raw else blog_url

        # Pad list so index access is always safe
        while len(x_posts) < 3:
            x_posts.append(x_posts[0] if x_posts else "")

        # -- Tuesday --------------------------------------------------
        # X post 1
        try:
            _at = next_tuesday_x()
            result["x_1"] = post_to_x(x_posts[0], scheduled_at=_at)
            logger.info("X post 1 scheduled %s", _at)
        except Exception as e:
            logger.error("X post 1 failed: %s", e)

        # -- Wednesday ------------------------------------------------
        # Threads
        try:
            if threads_text:
                _at = next_wednesday_threads()
                result["threads"] = post_to_threads(threads_text, scheduled_at=_at)
                logger.info("Threads scheduled %s", _at)
        except Exception as e:
            logger.error("Threads failed: %s", e)

        # Reddit — posted immediately
        try:
            reddit_title, reddit_body = _parse_reddit_post(social_text)
            if reddit_title and blog_url:
                from reddit_poster import post_to_reddit
                reddit_urls = post_to_reddit(
                    title=reddit_title,
                    body=reddit_body,
                    url=blog_url,
                    pillar=meta.get("pillar", ""),
                    persona=meta.get("persona", ""),
                )
                result["reddit"] = reddit_urls
                logger.info("Reddit: posted to %d subreddit(s)", len(reddit_urls))
        except Exception as e:
            logger.error("Reddit post failed for %s: %s", folder.name, e)

    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["status"]      = "done"

    # ── Save receipt (prevents re-posting) ──────────────────────────────
    receipt_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    logger.info("Receipt saved: %s", receipt_path)
    return result


# ---------------------------------------------------------------------------
# Scan all unpublished output folders
# ---------------------------------------------------------------------------

def process_all(
    output_dir: Path = OUTPUT_DIR,
    cards_only: bool = False,
    video_only: bool = False,
    skip_post: bool = False,
) -> list[dict]:
    folders = sorted(
        [f for f in output_dir.iterdir() if f.is_dir()],
        key=lambda f: f.name,
    )
    if not folders:
        logger.info("No output folders found in %s", output_dir)
        return []

    # Fetch live Substack slugs once for the whole run
    live_slugs = _live_substack_slugs()
    if not live_slugs:
        logger.error("Could not fetch live articles from Substack — aborting to avoid posting dead links")
        return []

    results = []
    for folder in folders:
        if (folder / "social_posted.json").exists():
            continue  # already done
        if not (folder / "06_social.md").exists():
            continue  # no social copy yet
        if not _is_published(folder, live_slugs=live_slugs):
            logger.info("Skipping (not live on Substack): %s", folder.name)
            continue
        logger.info("Processing: %s", folder.name)
        r = process_folder(folder, cards_only=cards_only,
                           video_only=video_only, skip_post=skip_post,
                           live_slugs=live_slugs)
        results.append(r)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _folder_for_url(url: str, output_dir: Path) -> Optional[Path]:
    """Find the output folder whose slug matches a Substack post URL.
    Accepts URLs like:
      https://joinivyedge.substack.com/p/rto-mandates-have-a-gender-problem
      https://joinivyedge.substack.com/p/197227134          (numeric ID fallback)
    """
    # Extract the slug/id from the URL path
    slug = url.rstrip("/").split("/")[-1]

    # Try exact folder-name suffix match first (most reliable)
    for folder in output_dir.iterdir():
        if not folder.is_dir():
            continue
        # Strip the date prefix and compare
        folder_slug = folder.name[11:] if len(folder.name) > 11 else folder.name  # skip YYYY-MM-DD_
        if folder_slug.startswith(slug[:50]) or slug[:50] in folder_slug:
            return folder

    # Try matching on substack_url.txt stored in the folder
    for folder in output_dir.iterdir():
        if not folder.is_dir():
            continue
        url_file = folder / "substack_url.txt"
        if url_file.exists() and slug in url_file.read_text():
            return folder

    # Try matching on blog_url.txt
    for folder in output_dir.iterdir():
        if not folder.is_dir():
            continue
        blog_file = folder / "blog_url.txt"
        if blog_file.exists() and slug in blog_file.read_text():
            return folder

    return None


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ivy Edge social media agent")
    parser.add_argument("--url", help="Substack article URL to post on social media "
                        "(e.g. https://joinivyedge.substack.com/p/rto-mandates-have-a-gender-problem)")
    parser.add_argument("--folder", help="Process a single output folder (by path)")
    parser.add_argument("--output-dir", default=str(OUTPUT_DIR),
                        help="Output directory to scan (default: output/)")
    parser.add_argument("--cards-only", action="store_true",
                        help="Generate image cards only — skip video and posting")
    parser.add_argument("--video-only", action="store_true",
                        help="Generate videos only — skip cards and posting")
    parser.add_argument("--no-post", action="store_true",
                        help="Generate assets but do not post to social media")
    args = parser.parse_args(argv)

    output_dir = Path(args.output_dir)

    # --url: find folder by Substack URL
    if args.url:
        folder = _folder_for_url(args.url, output_dir)
        if not folder:
            print(f"No output folder found matching URL: {args.url}", file=sys.stderr)
            print(f"Tip: make sure the article was generated by the pipeline and has a folder in {output_dir}/", file=sys.stderr)
            return 1
        logger.info("Matched URL to folder: %s", folder.name)
        # Verify the article is actually live on Substack right now
        live_slugs = _live_substack_slugs()
        if not _is_published(folder, live_slugs=live_slugs):
            print(
                f"\n⚠️  '{folder.name}' is not currently live on Substack.\n"
                "   Posting social links to an unpublished article will send followers to a dead URL.\n"
                "   Publish the article on Substack first, then re-run with --url.\n",
                file=sys.stderr,
            )
            return 1
        # Remove receipt if it exists so we can re-run for this article
        receipt = folder / "social_posted.json"
        if receipt.exists():
            receipt.unlink()
            logger.info("Cleared existing receipt — re-running social for this article")
        result = process_folder(
            folder,
            cards_only=args.cards_only,
            video_only=args.video_only,
            skip_post=args.no_post,
            live_slugs=live_slugs,
        )
        print(json.dumps(result, indent=2))
        return 0

    # --folder: find folder by path
    if args.folder:
        folder = Path(args.folder)
        if not folder.exists():
            print(f"Folder not found: {folder}", file=sys.stderr)
            return 1
        result = process_folder(
            folder,
            cards_only=args.cards_only,
            video_only=args.video_only,
            skip_post=args.no_post,
        )
        print(json.dumps(result, indent=2))
        return 0

    results = process_all(
        output_dir=Path(args.output_dir),
        cards_only=args.cards_only,
        video_only=args.video_only,
        skip_post=args.no_post,
    )

    done    = [r for r in results if r.get("status") == "done"]
    skipped = [r for r in results if r.get("status") == "already_posted"]

    print(f"\nDone: {len(done)} posts processed, {len(skipped)} already posted.")
    for r in done:
        ig  = r.get("instagram") or "—"
        thr = r.get("threads")   or "—"
        vid = "✓" if r.get("video") else "—"
        print(f"  {Path(r['folder']).name}")
        print(f"    Instagram: {ig}")
        print(f"    Threads:   {thr}")
        print(f"    Video:     {vid}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
