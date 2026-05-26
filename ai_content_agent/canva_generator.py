"""
IvyEdge Canva Generator

Generates branded image cards (and optionally video stills) using the
Canva Connect API and your brand templates.

Setup (one time):
  python canva_generator.py --auth          # Opens browser, stores refresh token
  python canva_generator.py --list-templates # Shows your brand template IDs

Then add to .env:
  CANVA_IMAGE_TEMPLATE_ID=AAFa...           # Your image card template
  CANVA_VIDEO_TEMPLATE_ID=AAFb...           # Optional: video background template

Required .env:
  CANVA_CLIENT_ID=OC-...
  CANVA_CLIENT_SECRET=...
  CANVA_REFRESH_TOKEN=...                   # Written by --auth

Usage in pipeline:
  from canva_generator import generate_image_card
  png_path = generate_image_card(
      title="Your article title",
      hook="The one-line hook from the social post",
      output_path=Path("output/article/07_image_card.png"),
  )
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv, set_key

load_dotenv(Path(__file__).parent / ".env", override=True)

logger = logging.getLogger("ivyedge.canva")

CANVA_CLIENT_ID      = os.getenv("CANVA_CLIENT_ID", "")
CANVA_CLIENT_SECRET  = os.getenv("CANVA_CLIENT_SECRET", "")
CANVA_REFRESH_TOKEN  = os.getenv("CANVA_REFRESH_TOKEN", "")
CANVA_IMAGE_TEMPLATE = os.getenv("CANVA_IMAGE_TEMPLATE_ID", "")
CANVA_VIDEO_TEMPLATE = os.getenv("CANVA_VIDEO_TEMPLATE_ID", "")

CANVA_API_BASE  = "https://api.canva.com/rest/v1"
CANVA_AUTH_URL  = "https://www.canva.com/api/oauth/authorize"
CANVA_TOKEN_URL = "https://api.canva.com/rest/v1/oauth/token"
REDIRECT_URI    = "http://127.0.0.1:8765/callback"

ENV_FILE = Path(__file__).parent / ".env"

# Scopes needed for autofill + export
SCOPES = [
    "asset:read",
    "asset:write",
    "brandtemplate:content:read",
    "brandtemplate:meta:read",
    "design:content:read",
    "design:content:write",
    "design:meta:read",
    "design:meta:write",
]


# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    verifier  = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _exchange_code(code: str, verifier: str) -> dict:
    resp = requests.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type":    "authorization_code",
            "code":          code,
            "redirect_uri":  REDIRECT_URI,
            "code_verifier": verifier,
        },
        auth=(CANVA_CLIENT_ID, CANVA_CLIENT_SECRET),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _refresh_access_token(refresh_token: str) -> dict:
    resp = requests.post(
        CANVA_TOKEN_URL,
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
        },
        auth=(CANVA_CLIENT_ID, CANVA_CLIENT_SECRET),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# Cache the access token in memory for the process lifetime
_token_cache: dict = {}


def get_access_token() -> str:
    """Return a valid access token, refreshing if needed."""
    global _token_cache
    now = time.time()
    if _token_cache.get("expires_at", 0) > now + 60:
        return _token_cache["access_token"]

    refresh = CANVA_REFRESH_TOKEN or os.getenv("CANVA_REFRESH_TOKEN", "")
    if not refresh:
        raise ValueError(
            "No CANVA_REFRESH_TOKEN in .env — run: python canva_generator.py --auth"
        )

    tokens = _refresh_access_token(refresh)
    _token_cache = {
        "access_token": tokens["access_token"],
        "expires_at":   now + tokens.get("expires_in", 3600),
    }
    # If a new refresh token is issued, persist it
    if "refresh_token" in tokens:
        set_key(str(ENV_FILE), "CANVA_REFRESH_TOKEN", tokens["refresh_token"])
        os.environ["CANVA_REFRESH_TOKEN"] = tokens["refresh_token"]

    logger.info("Canva access token refreshed.")
    return _token_cache["access_token"]


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {get_access_token()}",
        "Content-Type":  "application/json",
    }


# ---------------------------------------------------------------------------
# One-time browser auth flow
# ---------------------------------------------------------------------------

def run_auth_flow() -> None:
    """Open a browser for OAuth, catch the callback, store tokens in .env."""
    if not CANVA_CLIENT_ID or not CANVA_CLIENT_SECRET:
        raise ValueError("Set CANVA_CLIENT_ID and CANVA_CLIENT_SECRET in .env first.")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    params = {
        "client_id":             CANVA_CLIENT_ID,
        "response_type":         "code",
        "redirect_uri":          REDIRECT_URI,
        "scope":                 " ".join(SCOPES),
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    auth_url = f"{CANVA_AUTH_URL}?{urllib.parse.urlencode(params)}"

    received: dict = {}

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass  # silence server logs

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            qs = urllib.parse.parse_qs(parsed.query)
            received["code"]  = qs.get("code",  [""])[0]
            received["state"] = qs.get("state", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>IvyEdge: Canva authorised! You can close this tab.</h2>")

    server = HTTPServer(("127.0.0.1", 8765), _Handler)
    server.timeout = 120

    print(f"\nOpening Canva authorization page...")
    webbrowser.open(auth_url)
    print("Waiting for authorization (120s timeout)...")
    server.handle_request()

    if not received.get("code"):
        raise RuntimeError("No authorization code received.")
    if received.get("state") != state:
        raise RuntimeError("State mismatch — possible CSRF.")

    tokens = _exchange_code(received["code"], verifier)
    set_key(str(ENV_FILE), "CANVA_REFRESH_TOKEN", tokens["refresh_token"])
    print(f"\n✅ Authorized! Refresh token saved to .env")
    print(f"   Access token expires in {tokens.get('expires_in', '?')}s")


# ---------------------------------------------------------------------------
# Brand templates
# ---------------------------------------------------------------------------

def list_brand_templates() -> list[dict]:
    """Return all brand templates available to the account."""
    templates = []
    params = {"limit": 50}
    while True:
        resp = requests.get(
            f"{CANVA_API_BASE}/brand-templates",
            headers=_headers(),
            params=params,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        templates.extend(data.get("items", []))
        continuation = data.get("continuation")
        if not continuation:
            break
        params["continuation"] = continuation
    return templates


def get_template_dataset(template_id: str) -> dict:
    """Return the autofill field names available in a brand template."""
    resp = requests.get(
        f"{CANVA_API_BASE}/brand-templates/{template_id}/dataset",
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Autofill + export
# ---------------------------------------------------------------------------

def _create_autofill_job(template_id: str, title: str, data: dict) -> str:
    """Start an autofill job. Returns job ID."""
    payload = {
        "brandTemplateId": template_id,
        "designTitle":     title,
        "autofillData": {
            "type":   "autofill_data",
            "data":   data,
        },
    }
    resp = requests.post(
        f"{CANVA_API_BASE}/autofills",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["job"]["id"]


def _wait_for_autofill(job_id: str, timeout: int = 120) -> str:
    """Poll until autofill job completes. Returns design ID."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            f"{CANVA_API_BASE}/autofills/{job_id}",
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        job = resp.json()["job"]
        status = job.get("status")
        if status == "success":
            return job["result"]["design"]["id"]
        if status == "failed":
            raise RuntimeError(f"Canva autofill job failed: {job}")
        time.sleep(2)
    raise TimeoutError(f"Autofill job {job_id} timed out after {timeout}s")


def _create_export_job(design_id: str, file_type: str = "png") -> str:
    """Start an export job. Returns job ID."""
    payload = {
        "designId": design_id,
        "format":   {
            "type": file_type,
        },
    }
    resp = requests.post(
        f"{CANVA_API_BASE}/exports",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["job"]["id"]


def _wait_for_export(job_id: str, timeout: int = 120) -> list[str]:
    """Poll until export completes. Returns list of download URLs."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        resp = requests.get(
            f"{CANVA_API_BASE}/exports/{job_id}",
            headers=_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        job = resp.json()["job"]
        status = job.get("status")
        if status == "success":
            return [u["url"] for u in job.get("urls", [])]
        if status == "failed":
            raise RuntimeError(f"Canva export job failed: {job}")
        time.sleep(2)
    raise TimeoutError(f"Export job {job_id} timed out after {timeout}s")


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        dest.write_bytes(r.content)
    return dest


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_image_card(
    title: str,
    hook: str,
    output_path: Path,
    template_id: Optional[str] = None,
    extra_fields: Optional[dict] = None,
) -> Path:
    """
    Generate a branded image card from a Canva brand template.

    The template should have text fields named 'title' and 'hook'
    (check with --list-templates --fields <id>). Extra fields can be
    passed as a dict of {field_name: text_value}.

    Returns the path to the saved PNG.
    """
    tid = template_id or CANVA_IMAGE_TEMPLATE
    if not tid:
        raise ValueError(
            "No template ID — set CANVA_IMAGE_TEMPLATE_ID in .env or pass template_id."
        )

    # Build autofill data — Canva text fields use type "text"
    autofill_data: dict = {
        "title": {"type": "text", "text": title},
        "hook":  {"type": "text", "text": hook},
    }
    if extra_fields:
        for k, v in extra_fields.items():
            autofill_data[k] = {"type": "text", "text": str(v)}

    logger.info("Canva: starting autofill job for '%s'", title[:50])
    job_id   = _create_autofill_job(tid, f"IvyEdge — {title[:60]}", autofill_data)
    design_id = _wait_for_autofill(job_id)
    logger.info("Canva: autofill done → design %s", design_id)

    export_job_id = _create_export_job(design_id, file_type="png")
    urls = _wait_for_export(export_job_id)
    if not urls:
        raise RuntimeError("Canva export returned no download URLs")

    path = _download(urls[0], output_path)
    logger.info("Canva: image card saved → %s", path)
    return path


def generate_video_still(
    title: str,
    hook: str,
    output_path: Path,
    template_id: Optional[str] = None,
) -> Path:
    """
    Export a Canva video template as MP4.
    The template should be a video/animated design with 'title' and 'hook' fields.
    """
    tid = template_id or CANVA_VIDEO_TEMPLATE
    if not tid:
        raise ValueError(
            "No video template ID — set CANVA_VIDEO_TEMPLATE_ID in .env or pass template_id."
        )

    autofill_data = {
        "title": {"type": "text", "text": title},
        "hook":  {"type": "text", "text": hook},
    }

    logger.info("Canva: starting video autofill job for '%s'", title[:50])
    job_id    = _create_autofill_job(tid, f"IvyEdge Video — {title[:55]}", autofill_data)
    design_id = _wait_for_autofill(job_id)
    logger.info("Canva: video autofill done → design %s", design_id)

    export_job_id = _create_export_job(design_id, file_type="mp4")
    urls = _wait_for_export(export_job_id)
    if not urls:
        raise RuntimeError("Canva export returned no download URLs")

    path = _download(urls[0], output_path)
    logger.info("Canva: video saved → %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if "--auth" in sys.argv:
        run_auth_flow()

    elif "--list-templates" in sys.argv:
        templates = list_brand_templates()
        if not templates:
            print("No brand templates found. Create one in Canva first.")
        else:
            print(f"\nFound {len(templates)} brand template(s):\n")
            for t in templates:
                print(f"  Name: {t.get('name') or t.get('title', '(untitled)')}")
                print(f"  ID:   {t['id']}")
                print()
            print("Add the IDs you want to use to .env:")
            print("  CANVA_IMAGE_TEMPLATE_ID=...")
            print("  CANVA_VIDEO_TEMPLATE_ID=... (optional)")

    elif "--fields" in sys.argv:
        idx = sys.argv.index("--fields")
        if idx + 1 >= len(sys.argv):
            print("Usage: python canva_generator.py --fields <template_id>")
            sys.exit(1)
        tid = sys.argv[idx + 1]
        dataset = get_template_dataset(tid)
        print(f"\nAutofill fields for template {tid}:\n")
        for name, info in dataset.get("dataset", {}).items():
            print(f"  {name!r} → type: {info.get('type', '?')}")
        print("\nUse these field names in generate_image_card(extra_fields={...})")

    elif "--test" in sys.argv:
        out = Path("/tmp/canva_test_card.png")
        generate_image_card(
            title="Test Article Title",
            hook="This is a test hook line.",
            output_path=out,
        )
        print(f"Saved: {out}")

    else:
        print("Usage:")
        print("  python canva_generator.py --auth              # One-time browser auth")
        print("  python canva_generator.py --list-templates    # Find your template IDs")
        print("  python canva_generator.py --fields <id>       # See fields in a template")
        print("  python canva_generator.py --test              # Generate a test card")
