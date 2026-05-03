"""
Substack publisher for IvyEdge.

Converts a markdown draft to Substack's ProseMirror JSON format, creates a
draft, then publishes it. Authentication uses the substack.sid session cookie.

Usage:
    publisher = SubstackPublisher()
    url = publisher.publish(title="...", body_markdown="...", subtitle="...")
    print(url)
"""

import json
import logging
import os
import re
from urllib.parse import unquote

import markdown
import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("ivyedge.substack")

PUBLICATION_HOST = "joinivyedge.substack.com"
AUTHOR_ID = 502617299  # IvyEdge Substack author ID
BASE_URL = f"https://{PUBLICATION_HOST}/api/v1"


class SubstackPublisher:
    def __init__(self, sid_cookie: str | None = None):
        raw_sid = sid_cookie or os.getenv("SUBSTACK_SID", "")
        sid = unquote(raw_sid)
        if not sid:
            raise ValueError(
                "No Substack session cookie found. Set SUBSTACK_SID in .env "
                "or pass sid_cookie= to SubstackPublisher()."
            )
        self.session = requests.Session()
        self.session.cookies.set("substack.sid", sid, domain="substack.com")
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0",
            "Content-Type": "application/json",
            "Referer": f"https://{PUBLICATION_HOST}",
        })

    def _html_to_prosemirror(self, html: str) -> str:
        """Wrap HTML in Substack's minimal ProseMirror doc envelope."""
        paragraphs = []
        for block in re.split(r"\n{2,}", html.strip()):
            block = block.strip()
            if not block:
                continue
            paragraphs.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": re.sub(r"<[^>]+>", "", block)}],
            })
        doc = {"type": "doc", "content": paragraphs or [{"type": "paragraph"}]}
        return json.dumps(doc)

    def _markdown_to_prosemirror(self, md: str) -> str:
        html = markdown.markdown(md, extensions=["extra", "nl2br", "sane_lists"])
        return self._html_to_prosemirror(html)

    def _create_draft(self, title: str, body_markdown: str, subtitle: str = "") -> int:
        payload = {
            "type": "newsletter",
            "draft_title": title,
            "draft_subtitle": subtitle,
            "draft_body": self._markdown_to_prosemirror(body_markdown),
            "draft_bylines": [{"id": AUTHOR_ID, "is_guest": False}],
            "audience": "everyone",
            "section_chosen": True,
        }
        resp = self.session.post(f"{BASE_URL}/drafts", json=payload, timeout=30)
        if resp.status_code == 401:
            raise RuntimeError(
                "Substack authentication failed — SUBSTACK_SID cookie may have expired. "
                "Grab a fresh one from Chrome DevTools and update .env."
            )
        if not resp.ok:
            raise RuntimeError(f"Substack create draft error {resp.status_code}: {resp.text[:400]}")
        draft_id = resp.json().get("id")
        logger.info("Draft created: id=%s title='%s'", draft_id, title)
        return draft_id

    def _publish_draft(self, draft_id: int) -> str:
        resp = self.session.post(
            f"{BASE_URL}/drafts/{draft_id}/publish",
            json={"send_email": True, "share_automatically": False},
            timeout=30,
        )
        if not resp.ok:
            raise RuntimeError(f"Substack publish error {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
        url = (
            data.get("canonical_url")
            or data.get("url")
            or f"https://{PUBLICATION_HOST}/p/{draft_id}"
        )
        logger.info("Published: %s", url)
        return url

    def publish(self, title: str, body_markdown: str, subtitle: str = "") -> str:
        """Create a draft and publish it immediately. Returns the post URL."""
        draft_id = self._create_draft(title, body_markdown, subtitle)
        return self._publish_draft(draft_id)

    def create_draft_only(self, title: str, body_markdown: str, subtitle: str = "") -> int:
        """Create a draft without publishing. Returns the draft ID."""
        return self._create_draft(title, body_markdown, subtitle)
