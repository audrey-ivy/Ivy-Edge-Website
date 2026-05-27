"""
Ivy Edge Video Generator

Produces a branded TikTok/Reels MP4 from the TikTok script in 06_social.md.

Pipeline:
  1. Parse text from the TikTok script section of 06_social.md
  2. Split into readable phrases (sentence-boundary aware)
  3. Load a 30-second chunk of background music (cycles across posts)
  4. Loop ivy background video to match audio duration
  5. Render phrases evenly timed across the content window
  6. Composite: background + text overlays + music → output MP4

Required assets:
  assets/ivy_background.mp4
  assets/background_music.mp3   (3-minute track; sliced into 30-second chunks)
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont

load_dotenv(Path(__file__).parent / ".env", override=True)

logger = logging.getLogger("ivyedge.video")

BACKGROUND_VIDEO  = Path(__file__).parent / "assets" / "ivy_background.mp4"
BACKGROUND_MUSIC  = Path(__file__).parent / "assets" / "background_music.mp3"
ASSETS_DIR        = Path(__file__).parent / "assets"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

FOREST_GREEN = (28, 99, 80)
CORAL_PINK   = (255, 123, 156)
WHITE        = (255, 255, 255)
MINT         = (156, 227, 208)

VIDEO_W, VIDEO_H = 1080, 1920

CHUNK_DURATION     = 30.0  # seconds per video (one music chunk)
END_CARD_DURATION  = 2.5   # end-card holds at the tail
CONTENT_DURATION   = CHUNK_DURATION - END_CARD_DURATION  # 27.5 s for text

WORDS_PER_PHRASE   = 7     # target words per on-screen phrase
MIN_PHRASE_SECONDS = 3.0   # minimum time each phrase stays on screen
MAX_PHRASES        = int(CONTENT_DURATION / MIN_PHRASE_SECONDS)  # = 9


# ---------------------------------------------------------------------------
# Script parser
# ---------------------------------------------------------------------------

def parse_tiktok_script(social_md: str, script_index: int = 1) -> str:
    """Extract spoken dialogue lines from the TikTok script.

    script_index: 1 = Script 1 (default / first video), 2 = Script 2 (second video).
    Falls back to the legacy single-script format if numbered scripts are not found.
    """
    # New format: ### Script 1 / ### Script 2
    numbered_match = re.search(
        rf"###\s*Script {script_index}\s*\n(.*?)(?=\n###\s*Script|\n###\s*Hook options|\n##|\Z)",
        social_md, re.DOTALL
    )
    if numbered_match:
        tiktok_match = numbered_match
    else:
        # Legacy: single ### Script section (also handles script_index=1 fallback)
        tiktok_match = re.search(
            r"###\s*Script\s*\n(.*?)(?=\n###\s*Production|\Z)",
            social_md, re.DOTALL
        )
    if not tiktok_match:
        tiktok_match = re.search(
            r"##\s*TikTok.*?\n(.*?)(?=\n##\s*Production notes|\Z)",
            social_md, re.DOTALL | re.IGNORECASE
        )

    script_text = tiktok_match.group(1) if tiktok_match else social_md

    # Regex to match bare URLs (http/https or www.)
    URL_RE = re.compile(r'https?://\S+|www\.\S+')

    spoken_lines: list[str] = []
    for line in script_text.splitlines():
        line = line.strip()
        if not line or line.startswith("[") or line.startswith("#"):
            continue
        # Drop lines that are nothing but a URL
        if URL_RE.fullmatch(line):
            continue
        # Replace inline URLs with the short branded domain
        line = URL_RE.sub("ivyedge.co", line)
        spoken_lines.append(line)

    return " ".join(spoken_lines).strip()


# ---------------------------------------------------------------------------
# Music chunk loader
# ---------------------------------------------------------------------------

def _load_music_chunk(chunk_index: int = 0) -> "AudioFileClip":
    """
    Load a CHUNK_DURATION-second slice of background_music.mp3.
    chunk_index cycles through available chunks so each post sounds different.
    """
    from moviepy import AudioFileClip

    if not BACKGROUND_MUSIC.exists():
        raise FileNotFoundError(
            f"Background music not found: {BACKGROUND_MUSIC}\n"
            "Add assets/background_music.mp3 to use music-mode video."
        )

    full = AudioFileClip(str(BACKGROUND_MUSIC))
    total_chunks = max(1, int(full.duration // CHUNK_DURATION))
    idx   = chunk_index % total_chunks
    start = idx * CHUNK_DURATION
    end   = min(start + CHUNK_DURATION, full.duration)

    chunk = full.subclipped(start, end)

    # If the chunk is shorter than needed (track shorter than CHUNK_DURATION),
    # loop it to fill the full duration
    if chunk.duration < CHUNK_DURATION:
        from moviepy import concatenate_audioclips
        loops = int(CHUNK_DURATION / chunk.duration) + 2
        chunk = concatenate_audioclips([chunk] * loops).subclipped(0, CHUNK_DURATION)

    # Gentle fade in/out so the cut doesn't feel abrupt (moviepy 2.x API)
    from moviepy.audio.fx import AudioFadeIn, AudioFadeOut
    chunk = chunk.with_effects([AudioFadeIn(0.8), AudioFadeOut(1.5)])
    logger.info(
        "Music chunk %d/%d (%.1f–%.1f s of %.1f s track)",
        idx + 1, total_chunks, start, end, full.duration,
    )
    return chunk


# ---------------------------------------------------------------------------
# Phrase splitter (no timestamps needed)
# ---------------------------------------------------------------------------

def _split_into_phrases(text: str) -> list[str]:
    """
    Split script text into display phrases, always breaking at sentence endings.
    Returns list of phrase strings; caller assigns timing.
    """
    SENTENCE_END = re.compile(r'[.!?]["\']?\s*$')
    HARD_CAP = int(WORDS_PER_PHRASE * 1.5)

    words   = text.split()
    phrases: list[str] = []
    chunk:   list[str] = []

    for word in words:
        chunk.append(word)
        at_sentence_end = bool(SENTENCE_END.search(word))
        at_hard_cap     = len(chunk) >= HARD_CAP

        if at_sentence_end or at_hard_cap:
            phrases.append(" ".join(chunk))
            chunk = []

    if chunk:
        phrases.append(" ".join(chunk))

    return phrases


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

FONT_DIR = Path(__file__).parent / "assets" / "fonts"


def _load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / name
    if path.exists():
        try:
            return ImageFont.truetype(str(path), size)
        except Exception:
            pass
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Phrase frame renderer
# ---------------------------------------------------------------------------

def _make_phrase_frame(text: str, size: tuple[int, int]) -> Image.Image:
    """
    Render a single text phrase as a transparent RGBA overlay.
    Large white Fraunces text with dark outline for legibility over any background.
    """
    img  = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    w, h = size
    pad  = 36

    font_size = 140
    font = _load_font("Fraunces.ttf", font_size)

    words  = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] > w - 2 * pad and current:
            lines.append(current)
            current = word
        else:
            current = test
    if current:
        lines.append(current)

    line_h = font_size + 20
    text_h = len(lines) * line_h
    text_y = (h - text_h) // 2 + 160

    outline = 4
    for line in lines:
        bbox   = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x      = (w - line_w) // 2
        for ox in range(-outline, outline + 1):
            for oy in range(-outline, outline + 1):
                if ox == 0 and oy == 0:
                    continue
                draw.text((x + ox, text_y + oy), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, text_y), line, font=font, fill=(*WHITE, 255))
        text_y += line_h

    # Watermark
    wm_font = _load_font("DMSans.ttf", 30)
    wm_text = "ivyedge.co"
    wm_bbox = draw.textbbox((0, 0), wm_text, font=wm_font)
    draw.text(
        (w - wm_bbox[2] - 40, h - 64),
        wm_text, font=wm_font, fill=(*MINT, 200),
    )

    return img


# ---------------------------------------------------------------------------
# Video assembly
# ---------------------------------------------------------------------------

def generate_video(
    social_md_path: Path,
    output_path: Path,
    title: str = "",
    chunk_index: Optional[int] = None,
    script_index: int = 1,
) -> Path:
    """
    Generate a branded TikTok/Reels MP4 with music and timed text overlays.

    A 30-second chunk of background_music.mp3 drives the video length.
    Text phrases are distributed evenly across the content window so each
    is on screen long enough to read comfortably (~3-4 seconds each).
    chunk_index selects which 30-second slice of the music to use;
    defaults to a hash of the title so different posts use different chunks.
    script_index: 1 or 2 — selects Script 1 or Script 2 from the social file.
    """
    try:
        from moviepy import VideoFileClip, AudioFileClip, CompositeVideoClip, ImageClip
        from moviepy import concatenate_videoclips
    except ImportError:
        raise ImportError("moviepy not installed. Run: pip install moviepy")

    if not BACKGROUND_VIDEO.exists():
        raise FileNotFoundError(f"Ivy background video not found: {BACKGROUND_VIDEO}")

    social_text = social_md_path.read_text(encoding="utf-8")
    spoken      = parse_tiktok_script(social_text, script_index=script_index)

    if not spoken:
        raise ValueError("No spoken dialogue found in TikTok script.")

    # Determine which music chunk to use
    if chunk_index is None:
        chunk_index = abs(hash(title or social_md_path.stem)) % 6

    audio_clip = _load_music_chunk(chunk_index)
    duration   = CHUNK_DURATION  # fixed 30-second video

    # ── Background (ping-pong loop: forward → backward → forward …) ─────
    from moviepy.video.fx import TimeMirror

    bg_src       = VideoFileClip(str(BACKGROUND_VIDEO), audio=False)
    total_needed = duration + END_CARD_DURATION
    one_frame    = 1.0 / bg_src.fps

    # Trim one frame from the end of each segment so the shared
    # boundary frame isn't duplicated at every transition.
    fwd = bg_src.subclipped(0, bg_src.duration - one_frame)
    rev = bg_src.with_effects([TimeMirror()]).subclipped(0, bg_src.duration - one_frame)

    # Build alternating forward/backward pairs until we have enough footage
    pairs_needed = int(total_needed / (fwd.duration * 2)) + 2
    segments = []
    for _ in range(pairs_needed):
        segments.append(fwd)
        segments.append(rev)

    bg = concatenate_videoclips(segments).subclipped(0, total_needed)

    bg_w, bg_h = bg.size
    scale  = max(VIDEO_W / bg_w, VIDEO_H / bg_h)
    new_w  = int(bg_w * scale)
    new_h  = int(bg_h * scale)
    bg     = bg.resized((new_w, new_h))
    x_off  = (new_w - VIDEO_W) // 2
    y_off  = (new_h - VIDEO_H) // 2
    bg     = bg.cropped(x1=x_off, y1=y_off, x2=x_off + VIDEO_W, y2=y_off + VIDEO_H)

    # ── Text phrase overlays ─────────────────────────────────────────────
    phrases = _split_into_phrases(spoken)

    # Cap to MAX_PHRASES so each phrase gets at least MIN_PHRASE_SECONDS on screen.
    # TikTok viewers need time to read — 1-second flashes are unreadable.
    if len(phrases) > MAX_PHRASES:
        logger.info(
            "Script has %d phrases — trimming to %d so each gets ≥%.0fs on screen",
            len(phrases), MAX_PHRASES, MIN_PHRASE_SECONDS,
        )
        phrases = phrases[:MAX_PHRASES]

    num_phrases = len(phrases)
    phrase_dur  = CONTENT_DURATION / num_phrases  # evenly spaced

    logger.info(
        "Text: %d phrases × %.1f s each (%.0f words total)",
        num_phrases, phrase_dur, len(spoken.split()),
    )

    import numpy as np
    overlay_clips = []

    for i, phrase_text in enumerate(phrases):
        start = i * phrase_dur
        end   = start + phrase_dur

        img  = _make_phrase_frame(phrase_text, (VIDEO_W, VIDEO_H))
        arr  = np.array(img)

        try:
            from moviepy.video.fx import CrossFadeIn
            clip = (
                ImageClip(arr, duration=phrase_dur)
                .with_start(start)
                .with_effects([CrossFadeIn(0.15)])
            )
        except Exception:
            clip = ImageClip(arr, duration=phrase_dur).with_start(start)

        overlay_clips.append(clip)

    # ── End card ─────────────────────────────────────────────────────────
    end_img  = Image.new("RGBA", (VIDEO_W, VIDEO_H), (0, 0, 0, 0))
    end_draw = ImageDraw.Draw(end_img)
    end_draw.rectangle([(0, 0), (VIDEO_W, VIDEO_H)], fill=(0, 5, 1, 180))

    ec_font = _load_font("Fraunces.ttf", 120)
    ec_text = "ivyedge.co"
    ec_bbox = end_draw.textbbox((0, 0), ec_text, font=ec_font)
    ec_w    = ec_bbox[2] - ec_bbox[0]
    ec_x    = (VIDEO_W - ec_w) // 2
    ec_y    = VIDEO_H // 2 - 100
    for ox in range(-4, 5):
        for oy in range(-4, 5):
            if ox == 0 and oy == 0:
                continue
            end_draw.text((ec_x + ox, ec_y + oy), ec_text, font=ec_font, fill=(0, 0, 0, 200))
    end_draw.text((ec_x, ec_y), ec_text, font=ec_font, fill=(*WHITE, 255))

    tag_font = _load_font("DMSans.ttf", 52)
    tag_text = "Grow through anything."
    tag_bbox = end_draw.textbbox((0, 0), tag_text, font=tag_font)
    tag_w    = tag_bbox[2] - tag_bbox[0]
    end_draw.text(
        ((VIDEO_W - tag_w) // 2, ec_y + 160),
        tag_text, font=tag_font, fill=(*MINT, 230),
    )

    end_arr  = np.array(end_img)
    end_clip = ImageClip(end_arr, duration=END_CARD_DURATION).with_start(CONTENT_DURATION)
    overlay_clips.append(end_clip)

    # ── Composite + export ────────────────────────────────────────────────
    final = CompositeVideoClip([bg] + overlay_clips, size=(VIDEO_W, VIDEO_H))
    final = final.with_audio(audio_clip)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    import tempfile, os
    with tempfile.TemporaryDirectory() as tmp:
        final.write_videofile(
            str(output_path),
            fps=30,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=os.path.join(tmp, "temp_audio.m4a"),
            remove_temp=True,
            logger=None,
        )

    logger.info("Video saved: %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    social_md = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    if not social_md or not social_md.exists():
        print("Usage: python video_generator.py path/to/06_social.md")
        sys.exit(1)
    out = social_md.parent / "08_video.mp4"
    generate_video(social_md, out)
    print(f"Video saved: {out}")
