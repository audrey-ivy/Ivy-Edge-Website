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

load_dotenv(override=True)

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
        """Convert HTML to Substack's ProseMirror doc format."""
        from bs4 import BeautifulSoup, NavigableString, Tag

        soup  = BeautifulSoup(html, "lxml")
        nodes = []

        def inline_content(el) -> list:
            """Recursively convert inline HTML to ProseMirror marks."""
            result = []
            for child in el.children:
                if isinstance(child, NavigableString):
                    text = str(child)
                    if text:
                        result.append({"type": "text", "text": text})
                elif isinstance(child, Tag):
                    marks = []
                    if child.name in ("strong", "b"):
                        marks = [{"type": "bold"}]
                    elif child.name in ("em", "i"):
                        marks = [{"type": "italic"}]
                    elif child.name == "a":
                        marks = [{"type": "link", "attrs": {"href": child.get("href", "")}}]
                    elif child.name == "code":
                        marks = [{"type": "code"}]
                    inner = inline_content(child)
                    for node in inner:
                        if marks:
                            existing = node.get("marks", [])
                            node["marks"] = existing + marks
                        result.append(node)
            return result

        HEADING_MAP = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}

        for el in soup.body.children if soup.body else soup.children:
            if isinstance(el, NavigableString):
                text = str(el).strip()
                if text:
                    nodes.append({"type": "paragraph",
                                  "content": [{"type": "text", "text": text}]})
                continue
            if not isinstance(el, Tag):
                continue

            tag = el.name

            if tag in HEADING_MAP:
                level = HEADING_MAP[tag]
                content = inline_content(el)
                if content:
                    nodes.append({
                        "type": "heading",
                        "attrs": {"level": level},
                        "content": content,
                    })

            elif tag == "p":
                content = inline_content(el)
                if content:
                    nodes.append({"type": "paragraph", "content": content})

            elif tag in ("ul", "ol"):
                list_type = "bullet_list" if tag == "ul" else "ordered_list"
                items = []
                for li in el.find_all("li", recursive=False):
                    li_content = inline_content(li)
                    if li_content:
                        items.append({
                            "type": "list_item",
                            "content": [{"type": "paragraph", "content": li_content}],
                        })
                if items:
                    nodes.append({"type": list_type, "content": items})

            elif tag == "blockquote":
                content = inline_content(el)
                if content:
                    nodes.append({
                        "type": "blockquote",
                        "content": [{"type": "paragraph", "content": content}],
                    })

            elif tag == "hr":
                nodes.append({"type": "horizontal_rule"})

            elif tag == "pre":
                code = el.find("code")
                text = code.get_text() if code else el.get_text()
                nodes.append({
                    "type": "code_block",
                    "content": [{"type": "text", "text": text}],
                })

        doc = {"type": "doc", "content": nodes or [{"type": "paragraph"}]}
        return json.dumps(doc)

    def _markdown_to_prosemirror(self, md: str) -> str:
        html = markdown.markdown(md, extensions=["extra", "sane_lists", "tables"])
        return self._html_to_prosemirror(html)

    # The exact IvyEdge standard footer — appended once after stripping any AI-generated copy
    _STANDARD_FOOTER = """

---

IvyEdge is being built for every woman who has been underestimated by a system that never genuinely evaluated her.

If that's you, we want you close when we launch.

[Get on the IvyEdge waitlist →](https://www.ivyedge.co)

*Be first. You've waited long enough.*
"""

    @staticmethod
    def _strip_duplicate_closing(md: str) -> str:
        """Remove the IvyEdge boilerplate block if the AI already wrote it,
        so we can append it exactly once. Preserves the article's own closing CTA."""
        BOILERPLATE = "IvyEdge is being built for every woman who has been underestimated"
        md = md.rstrip()
        if BOILERPLATE.lower() not in md.lower():
            return md
        # Split on --- dividers, drop any trailing section that contains the boilerplate
        sections = md.split("\n---\n")
        while sections and BOILERPLATE.lower() in sections[-1].lower():
            sections.pop()
        return "\n---\n".join(sections).rstrip()

    def _create_draft(self, title: str, body_markdown: str, subtitle: str = "", slug: str = "") -> int:
        body_with_footer = self._strip_duplicate_closing(body_markdown) + self._STANDARD_FOOTER
        payload = {
            "type": "newsletter",
            "draft_title": title,
            "draft_subtitle": subtitle,
            "draft_body": self._markdown_to_prosemirror(body_with_footer),
            "draft_bylines": [{"id": AUTHOR_ID, "is_guest": False}],
            "audience": "everyone",
            "section_chosen": True,
        }
        if slug:
            payload["draft_slug"] = slug
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

        # Prefer canonical_url from the publish response
        url = data.get("canonical_url") or data.get("url") or ""

        # If the response didn't include a real URL (common), fetch the post's slug
        if not url or url.endswith(str(draft_id)):
            post_resp = self.session.get(f"{BASE_URL}/drafts/{draft_id}", timeout=15)
            if post_resp.ok:
                post_data = post_resp.json()
                slug = post_data.get("slug", "")
                if slug:
                    url = f"https://{PUBLICATION_HOST}/p/{slug}"

        # Final fallback — numeric ID URLs return 404 publicly, log a warning
        if not url or "/" + str(draft_id) in url:
            logger.warning(
                "Could not resolve slug for draft %s — numeric ID URLs return 404. "
                "Check Substack dashboard for the real URL.", draft_id
            )
            url = f"https://{PUBLICATION_HOST}/p/{draft_id}"

        logger.info("Published: %s", url)
        return url

    def publish(self, title: str, body_markdown: str, subtitle: str = "", slug: str = "") -> str:
        """Create a draft and publish it to Substack. Returns the live post URL."""
        draft_id = self._create_draft(title, body_markdown, subtitle, slug=slug)
        url = self._publish_draft(draft_id)
        logger.info("Published: %s", url)
        return url

    def update_post(self, post_id: int, body_markdown: str, title: str = "", subtitle: str = "") -> bool:
        """Update the body (and optionally title/subtitle) of an existing published post.
        Substack allows editing published posts via PUT /api/v1/drafts/{id}
        (the same ID used at publish time — /api/v1/posts/{id} returns 404).
        Returns True on success."""
        body_with_footer = self._strip_duplicate_closing(body_markdown) + self._STANDARD_FOOTER
        body_pm = self._markdown_to_prosemirror(body_with_footer)
        payload: dict = {"draft_body": body_pm}
        if title:
            payload["draft_title"] = title
        if subtitle:
            payload["draft_subtitle"] = subtitle
        resp = self.session.put(f"{BASE_URL}/drafts/{post_id}", json=payload, timeout=30)
        if resp.status_code == 401:
            raise RuntimeError("Substack auth failed — refresh SUBSTACK_SID in .env")
        if not resp.ok:
            logger.error("Substack update error %s: %s", resp.status_code, resp.text[:400])
            return False
        logger.info("Post %s updated successfully", post_id)
        return True

    def list_published_posts(self) -> list[dict]:
        """Return all published posts as dicts with id, draft_title, slug."""
        all_posts: list[dict] = []
        cursor = None
        while True:
            params = "filter=published&limit=20"
            if cursor:
                params += f"&cursor={cursor}"
            resp = self.session.get(f"{BASE_URL}/drafts?{params}", timeout=30)
            if not resp.ok:
                logger.error("Substack list posts error %s", resp.status_code)
                break
            data = resp.json()
            all_posts.extend(data.get("posts", []))
            if not data.get("hasMore"):
                break
            cursor = data.get("nextCursor")
        return all_posts

    def create_draft_only(self, title: str, body_markdown: str, subtitle: str = "", slug: str = "") -> int:
        """Create a draft without publishing. Returns the draft ID."""
        return self._create_draft(title, body_markdown, subtitle, slug=slug)
