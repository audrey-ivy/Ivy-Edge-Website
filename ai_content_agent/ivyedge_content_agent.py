"""
Ivy Edge AI Content Agent
========================

A multi-step content generation pipeline that turns an editorial brief into a
publishable blog draft, while preserving Ivy Edge brand voice.

Pipeline: Research -> Outline -> Draft -> Voice Edit -> SEO

Usage (programmatic):
    from ivyedge_content_agent import IvyEdgeContentAgent

    agent = IvyEdgeContentAgent()  # picks up ANTHROPIC_API_KEY from env
    result = agent.generate_blog_post(
        topic="How freelancers can prove income stability",
        persona="Maya",
        pillar="Pillar 1: Financial Education for Non-Traditional Paths",
        keywords=["freelance income proof", "1099 loan approval", "freelancer credit"],
    )

Outputs are returned as a dict with every intermediate step plus the final
draft. The CLI runner (run_pipeline.py) writes them to disk.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

import anthropic
from dotenv import load_dotenv
from competitor_analysis import run_competitor_analysis

load_dotenv(override=True)

logger = logging.getLogger("ivyedge.agent")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.getenv("IVYEDGE_MODEL", "claude-sonnet-4-6")

# Token budgets per phase. Tune these against your typical output length.
PHASE_TOKEN_BUDGETS = {
    "research": 2500,
    "outline": 2500,
    "draft": 5000,
    "voice_edit": 5000,
    "seo": 5000,
    "social": 8000,
}

# Files in /context loaded on startup. Missing files are skipped with a warning
# rather than crashing — that lets you start with just brand_voice + personas
# and grow the library over time.
CORE_CONTEXT_FILES = {
    "brand_voice": "brand_voice.md",
    "personas": "personas.md",
    "product_knowledge": "product_knowledge.md",
    "strategy": "content_strategy.md",
    "inclusive_marketing": "inclusive_marketing.md",
}

# Folders walked recursively for additional context (research summaries,
# example articles). Each .md file is concatenated under its filename.
EXTRA_CONTEXT_DIRS = ["research_summaries", "examples"]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ArticleBrief:
    """Editorial brief for a single blog post."""
    topic: str
    persona: str  # "Priya" | "Maya" | "Carmen" | "Dominique" | "All"
    pillar: str
    primary_keyword: str
    secondary_keywords: list[str] = field(default_factory=list)
    content_format: str = "educational"  # educational | customer_story | behavioral | industry | contrarian
    target_word_count: tuple[int, int] = (1400, 1600)
    notes: str = ""

    @property
    def keyword_list(self) -> list[str]:
        return [self.primary_keyword] + self.secondary_keywords


@dataclass
class GenerationResult:
    """Full output of a generation run, including every intermediate step."""
    brief: ArticleBrief
    format_analysis: str = ""   # Phase 0 — competitive format benchmarks
    research: str = ""
    outline: str = ""
    first_draft: str = ""
    edited_draft: str = ""
    final_draft: str = ""
    social: str = ""
    barbie: str = ""
    meta_description: str = ""
    started_at: str = ""
    finished_at: str = ""
    model: str = DEFAULT_MODEL
    token_usage: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["brief"] = asdict(self.brief)
        return d


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

class IvyEdgeContentAgent:
    """
    Five-phase content generation agent for the Ivy Edge blog.

    The agent loads a context library (brand voice, personas, product knowledge,
    content strategy, plus any research summaries and example articles) and
    injects it into each phase's prompt. This keeps every draft on-brand
    without you having to re-paste guidelines into each request.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        context_dir: str | Path = "context",
    ):
        api_key = api_key or os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "No Anthropic API key found. Set ANTHROPIC_API_KEY in your "
                "environment or pass api_key=... to IvyEdgeContentAgent()."
            )

        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model or DEFAULT_MODEL
        self.context_dir = Path(context_dir)
        self.context = self._load_context()
        self._cumulative_usage = {"input_tokens": 0, "output_tokens": 0}

    # -- Context loading --------------------------------------------------

    def _load_context(self) -> dict[str, str]:
        """Load all markdown context files into memory.

        Returns a dict keyed by short name (brand_voice, personas, ...) with
        the markdown contents. Also loads an `extras` key containing the
        concatenated contents of /research_summaries and /examples.
        """
        if not self.context_dir.exists():
            raise FileNotFoundError(
                f"Context directory not found: {self.context_dir.resolve()}\n"
                "Create it (with brand_voice.md, personas.md, etc.) before "
                "running the agent."
            )

        ctx: dict[str, str] = {}
        for key, filename in CORE_CONTEXT_FILES.items():
            path = self.context_dir / filename
            if path.exists():
                ctx[key] = path.read_text(encoding="utf-8")
            else:
                logger.warning("Missing context file: %s (skipping)", path)
                ctx[key] = ""

        # Walk the extra directories — any .md inside is concatenated into
        # one big block, with a header noting which file it came from.
        extras: list[str] = []
        for sub in EXTRA_CONTEXT_DIRS:
            sub_dir = self.context_dir / sub
            if not sub_dir.exists():
                continue
            for md in sorted(sub_dir.rglob("*.md")):
                extras.append(f"## --- {sub}/{md.name} ---\n\n{md.read_text(encoding='utf-8')}\n")
        ctx["extras"] = "\n".join(extras)

        logger.info(
            "Loaded context: %s",
            {k: f"{len(v)} chars" for k, v in ctx.items()},
        )
        return ctx

    def reload_context(self) -> None:
        """Reload context files from disk — handy when you've just edited a
        guideline doc and want the change picked up without restarting."""
        self.context = self._load_context()

    # -- Low-level call helper -------------------------------------------

    def _call_claude(self, prompt: str, max_tokens: int, phase: str) -> str:
        """Wrap the Anthropic SDK call with logging + retry."""
        for attempt in range(3):
            try:
                start = time.time()
                msg = self.client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    messages=[{"role": "user", "content": prompt}],
                )
                elapsed = time.time() - start

                # Track usage so the CLI can report cost/tokens at the end
                if hasattr(msg, "usage") and msg.usage is not None:
                    self._cumulative_usage["input_tokens"] += getattr(msg.usage, "input_tokens", 0) or 0
                    self._cumulative_usage["output_tokens"] += getattr(msg.usage, "output_tokens", 0) or 0

                logger.info(
                    "[%s] %.1fs, in=%s out=%s",
                    phase,
                    elapsed,
                    getattr(msg.usage, "input_tokens", "?"),
                    getattr(msg.usage, "output_tokens", "?"),
                )
                return msg.content[0].text
            except (anthropic.RateLimitError, anthropic.APIStatusError) as e:
                wait = 2 ** attempt
                logger.warning("[%s] %s — retrying in %ss: %s", phase, type(e).__name__, wait, e)
                time.sleep(wait)
        raise RuntimeError(f"[{phase}] Claude call failed after 3 retries")

    # -- Prompt assembly --------------------------------------------------

    def _voice_block(self) -> str:
        """The same voice reminder shows up in every phase."""
        return (
            "# Ivy Edge brand voice (always)\n"
            "- Direct: lead with the answer; no hedging, no 'you might be wondering'.\n"
            "- Warm: acknowledge emotional reality; use contractions; say 'you'.\n"
            "- Grounded: tell the truth, even when uncomfortable; never over-promise.\n"
            "- Tagline to remember: 'Grow through anything.'\n\n"
            "## Words we use\n"
            "your money, your story, build, grow, here's how it works, "
            "you're in control, career gap, income pattern, trajectory, "
            "your full picture, no surprises\n\n"
            "## Words we avoid\n"
            "funds/monies, leverage, solutions, product suite, seamless, "
            "best-in-class, employment gap, unstable income, risk profile, "
            "tailored to your unique needs, please be advised\n\n"
            "## Voice calibration examples\n"
            "GOOD: 'Your 1099 income isn't unstable. Banks are measuring the wrong thing.'\n"
            "BAD:  'Freelance income may present challenges for traditional underwriting.'\n\n"
            "GOOD: 'Here's exactly what affects your credit score.'\n"
            "BAD:  'You might be wondering what impacts your credit score.'\n"
        )

    def _full_brand_context(self) -> str:
        """Pack the full context library into one block."""
        parts: list[str] = []
        for key in ("brand_voice", "personas", "product_knowledge", "strategy"):
            if self.context.get(key):
                parts.append(f"# === {key} ===\n\n{self.context[key]}")
        if self.context.get("extras"):
            parts.append(f"# === extra context ===\n\n{self.context['extras']}")

        # Always-present section: Ivy Edge's internal operating model.
        # This is both proof of mission and the factual foundation for
        # Pillar 6 (Building Differently) content.
        parts.append(
            "# === How Ivy Edge builds (the internal model) ===\n\n"
            "Ivy Edge practices what it preaches — the company is designed to not push women out:\n"
            "- 100% Remote: geography never a barrier. RTO mandates caused disproportionate "
            "female exits (Upwork 2024).\n"
            "- 32-Hour / 4-Day Work Week: Mondays off for caregiving or rest. 90% of companies "
            "in the world's largest trial kept it permanently (Scientific American 2025).\n"
            "- Dependent Care Credit: $400/month toward childcare, eldercare, or any dependent "
            "care through a company-sponsored DCAP. 455,000 women left the workforce in 2025; "
            "42% cited caregiving (Catalyst/BLS 2026).\n"
            "- 12 Weeks Paid Parental Leave: primary AND secondary caregiver. Phased return for "
            "the first month back. No penalty to pay, title, or trajectory. Average US maternity "
            "leave is just 7.2 weeks (Minneapolis Fed 2024).\n"
            "- Education Expenses/Reduction: reimbursement for higher education or equivalent "
            "paid toward student loans. Women hold roughly two-thirds of outstanding $1.7T "
            "student loan debt (Investopedia).\n"
            "- Learning & Wellness Budget: $2,000/year per person for professional development, "
            "mental health, therapy, or wellbeing. No approval needed. Only 36% of working "
            "caregivers report \"very good\" mental health (Guardian Life 2025).\n"
            "- All employees enrolled in the highest level of Ivy Circle membership.\n\n"
            "Tagline for this pillar: \"We can't truly live our mission while running a company "
            "that drives women out. We build differently.\""
        )

        return "\n\n".join(parts)

    # -- Phase 1: Research ------------------------------------------------

    def research_phase(self, brief: ArticleBrief, format_guidance: str = "") -> str:
        source_links_block = ""
        if format_guidance and "Recommended source links" in format_guidance:
            # Extract just the source links section so the researcher knows what URLs to use
            import re as _re
            m = _re.search(r"## Recommended source links\n([\s\S]+?)(?=\n## |\Z)", format_guidance)
            if m:
                source_links_block = (
                    "\nSOURCE LINKS FROM COMPETITOR ANALYSIS\n"
                    "Competitors are already citing these sources. Use them as a starting point.\n"
                    "For each stat you surface, try to match it to one of these URLs or find a\n"
                    "more specific page on the same domain. Include the full URL next to each stat.\n\n"
                    + m.group(1).strip() + "\n"
                )

        prompt = f"""You are a financial-services researcher preparing material for an Ivy Edge blog post.

IMPORTANT — PRE-LAUNCH CONTEXT
Ivy Edge has not launched any products yet. The blog exists to prove audience
demand for the Ivy Edge thesis. Do not reference Ivy Smart Loan, Ivy Credit
Builder, Ivy Credit Monitor, Ivy Checking, or any other Ivy Edge product as
if it exists. The goal is to demonstrate expertise on the topic and build
an audience — not to convert to a product.

ARTICLE BRIEF
- Topic: {brief.topic}
- Target persona: {brief.persona}
- Content pillar: {brief.pillar}
- Format: {brief.content_format}
- Primary keyword: {brief.primary_keyword}
- Secondary keywords: {", ".join(brief.secondary_keywords) or "(none)"}
- Notes from editor: {brief.notes or "(none)"}
{source_links_block}
RESEARCH TASKS
1. Identify 3-5 key insights about this topic that the target persona needs to know.
2. Surface relevant data points and statistics. For EVERY stat include a real URL
   in parentheses — e.g. "X% of borrowers (https://www.consumerfinance.gov/...)".
   Prefer .gov, .edu, CFPB, Federal Reserve, BLS, Experian, myFICO, Urban Institute.
   Use the source links above if they match; otherwise find the specific page.
3. Name what traditional finance gets wrong about this topic.
4. Identify the perspective Ivy Edge brings — the *point of view* on the topic,
   not a product pitch. Frame it as a thesis the reader can evaluate.
5. Suggest 1-2 anonymized examples or composite scenarios (NOT named member
   stories — we don't have members yet) that would resonate.

OUTPUT FORMAT (markdown)
## Key insights
- ...

## Relevant data (every stat must include its source URL in parentheses)
- [stat] (https://source-url)

## Traditional approach (what's broken)
- ...

## Ivy Edge angle
- ...

## Story or example ideas
- ...

{self._voice_block()}

# === Brand context ===
{self._full_brand_context()}
"""
        return self._call_claude(prompt, PHASE_TOKEN_BUDGETS["research"], "research")

    # -- Phase 2: Outline -------------------------------------------------

    def outline_phase(self, brief: ArticleBrief, research: str, format_guidance: str = "") -> str:
        format_block = (
            f"\nCOMPETITIVE FORMAT BENCHMARKS\n"
            f"The following analysis is based on the top free results for '{brief.primary_keyword}'.\n"
            f"Use it to set word count, heading structure, and section design.\n"
            f"Do not copy competitor angles — use this purely for structural guidance.\n\n"
            f"{format_guidance}\n"
        ) if format_guidance else ""

        contrarian_block = (
            "\nCONTRARIAN FORMAT INSTRUCTIONS\n"
            "This post challenges a widely-held belief. Structure:\n"
            "1. Name the common advice / belief clearly and fairly — don't strawman it\n"
            "2. Acknowledge why people believe it (it's not stupid — it's just incomplete)\n"
            "3. Introduce the counter-evidence or overlooked mechanism\n"
            "4. Give the reader the more accurate mental model\n"
            "5. Show what changes in practice if they adopt the new view\n"
            "The tone is curious and confident, never sneering. We're not dunking on bad advice —\n"
            "we're upgrading it. The common belief to challenge is in the article notes.\n"
        ) if brief.content_format == "contrarian" else ""

        prompt = f"""You are outlining an Ivy Edge blog post.

ARTICLE BRIEF
- Topic: {brief.topic}
- Persona: {brief.persona}
- Pillar: {brief.pillar}
- Format: {brief.content_format}
- Target length: {brief.target_word_count[0]}-{brief.target_word_count[1]} words
{format_block}{contrarian_block}
RESEARCH (from previous step)
{research}

PRE-LAUNCH CONTEXT
Ivy Edge has not launched any products. CTAs are audience-building actions —
not product applications. Use one of:
  - Join the Ivy Edge waitlist → link to https://www.ivyedge.co
  - Get the next post in your inbox (newsletter signup) → link to https://www.ivyedge.co
  - Tell us your story → link to https://www.instagram.com/ivyedge.co/
  - Share this with someone who needs it (organic distribution)
  - Take our 2-minute survey on [topic-relevant question] (audience research)

CTA LINK RULES (always use these exact URLs — never placeholders like /waitlist):
  - Waitlist / signup → https://www.ivyedge.co
  - "Tell us your story" → https://www.instagram.com/ivyedge.co/
  - Do NOT link to other /blog/ posts — Ivy Edge has no other published posts yet.

OUTLINE REQUIREMENTS
- Structure: Hook -> Problem -> Insight / point of view -> Practical steps -> CTA
- 3-5 H2 sections, each with 2-3 H3 subsections where useful
- Each section should call out the specific data point or example to use
- Each section ends with a key takeaway (one sentence the reader can action or repeat)
- End with a clear audience-building CTA from the list above

UNIQUE ANGLE REQUIREMENT
Before settling on the angle, consider: what would the top 5 Google results NOT say?
Competitor content on this topic is predictable. Ivy Edge's job is to say the thing
that is true but that no one else is saying — the insight that makes the reader feel
seen and smarter for having read it. Note the chosen angle in the outline.

OPENING HOOK — generate 3 options, each using a different technique:
  Option A — Surprising stat: lead with a specific, counterintuitive number
  Option B — Relatable question: ask the exact question the reader is already thinking
  Option C — Bold statement: make a direct claim that challenges conventional advice
Mark which one you recommend and why (one sentence).

PERSONA OPENING
The persona ({brief.persona}) is a real person, not a marketing character.
Open with a one-sentence scene or moment from her life that makes the financial
problem immediately real — before any statistics or explanations.
Example for Maya: "Maya sent her third invoice of the month the same week her
mortgage pre-approval was denied — for 'insufficient income history.'"

ANALOGIES
For any concept that typically causes eyes to glaze over (APR calculations, credit
utilisation mechanics, underwriting models), include a plain-world analogy that
makes it immediately intuitive. Note the analogy in the outline next to the concept.

OUTPUT FORMAT
- Working title (one option, plus 2 alternates)
- Chosen angle (and why it's not already on Google)
- Hook options A / B / C — with recommended pick
- Persona opening sentence
- Section list with: H2 header / key points / suggested stat or example / key takeaway
- Any analogies planned
- Proposed CTA (specific audience-building action — waitlist, newsletter, share, survey)

VOICE REMINDER: lead with the answer. Be direct. Make it immediately useful.

{self._voice_block()}

# === Brand context ===
{self._full_brand_context()}
"""
        return self._call_claude(prompt, PHASE_TOKEN_BUDGETS["outline"], "outline")

    # -- Phase 3: Draft ---------------------------------------------------

    def draft_phase(self, brief: ArticleBrief, outline: str) -> str:
        prompt = f"""You are writing a blog post for Ivy Edge based on this approved outline.

OUTLINE
{outline}

PRE-LAUNCH CONTEXT
Ivy Edge has not launched any products. Do NOT reference Ivy Smart Loan, Ivy
Credit Builder, Ivy Credit Monitor, Ivy Checking, or any specific product.
Refer to Ivy Edge as 'we' / 'us' — never as a product the reader can apply for.
The CTA must be audience-building only. Always use these exact URLs:
  - Waitlist / signup / "be first to know" → https://www.ivyedge.co
  - "Tell us your story" → https://www.instagram.com/ivyedge.co/
  - Do NOT add links to /blog/ paths — Ivy Edge has no other published posts yet.

WRITING GUIDELINES
- Voice: direct, warm, grounded. Ivy Edge is the brilliant friend who happens
  to work in finance — not a bank, not a wellness app.
- Use 'you' and contractions naturally.
- Lead each section with the answer, then explain.
- Short paragraphs (3-4 sentences).
- Concrete examples and specific numbers from the research.
- OPENING: Use the persona opening sentence from the outline. Start in scene —
  one specific moment, not a generic statement. Then bridge to the broader problem.
- ANALOGIES: For every abstract financial mechanism, include a plain-world analogy
  that makes the concept click on first read. The analogy should feel like something
  you'd say to a friend, not a textbook. ("Think of your credit utilisation like...")
- SHOW DON'T TELL: Don't say the reader "feels frustrated" — describe the situation
  that causes the frustration. Don't say a policy "disadvantages women" — show the
  exact mechanism and its consequence. Use specific scenes, numbers, and outcomes.
- SOURCES: Every statistic must be hyperlinked inline to its real source.
  Format: [description of source](https://actual-url.gov/...). Use government
  agencies, CFPB, Federal Reserve, BLS, AARP, .edu, or peer-reviewed research.
  No naked numbers — every data point gets a link.
- Subheadings for scanability.
- This is thought leadership: prove we understand the topic and the reader's
  reality better than anyone else writing about it.

WHAT TO AVOID
- Generic financial advice ('make a budget')
- Jargon without explanation
- Talking down to readers
- Over-promising results ('transform your credit in 30 days')
- Passive voice and corporate speak
- Hedging ('may', 'might', 'could potentially')
- ANY mention of Ivy Edge products as if they exist
- Phrases like 'apply today' or 'check your rate' — we have nothing to apply for

TARGET LENGTH: {brief.target_word_count[0]}-{brief.target_word_count[1]} words.

Return ONLY the blog post in clean markdown — no commentary, no wrappers.
Start with `# {{Working title}}` on the first line.

{self._voice_block()}

# === Brand context ===
{self._full_brand_context()}
"""
        return self._call_claude(prompt, PHASE_TOKEN_BUDGETS["draft"], "draft")

    # -- Phase 4: Voice edit ---------------------------------------------

    def voice_edit_phase(self, draft: str) -> str:
        prompt = f"""You are editing this Ivy Edge blog draft to strengthen the brand voice.

DRAFT
{draft}

EDITING CHECKLIST
1. Replace jargon and corporate language with Ivy Edge vocabulary.
2. Tighten the opening — does it lead with the answer?
3. Check for warmth — are we acknowledging emotional reality?
4. Remove hedging language ('may', 'might', 'could potentially').
5. Ensure contractions are used naturally.
6. Use 'you' — never 'borrowers', 'customers', 'consumers'.
7. Verify nothing over-promises ('guaranteed', 'transform', 'in 30 days').
8. Make sure practical steps are specific and actionable.
9. Vary sentence rhythm. Cut anything that sounds like a press release.
10. PRE-LAUNCH CHECK: Strip any reference to Ivy Smart Loan, Ivy Credit
    Builder, Ivy Credit Monitor, Ivy Checking, or 'apply' / 'check your
    rate' language. The CTA must be audience-building only.
    Always use these exact URLs — no placeholders like /waitlist:
      - Waitlist / signup → https://www.ivyedge.co
      - "Tell us your story" → https://www.instagram.com/ivyedge.co/
    Remove any markdown links to /blog/ paths — Ivy Edge has no other
    published posts yet. Either delete those links entirely or convert
    them to plain text (remove the link, keep the anchor text).
11. READABILITY: Target a Dale-Chall score of 8.5 (college-level, grades 13-15).
    - Replace multi-syllable jargon with precise but common words where meaning is preserved
    - Keep average sentence length 15-18 words; break up any sentence over 25 words
    - Financial terms (APR, FICO, 1099) are acceptable — explain once, then use freely
    - Never simplify to the point of imprecision; simplify to the point of clarity
    - Every paragraph should be readable on first pass; if a sentence needs re-reading, rewrite it

12. SOURCES: Convert every parenthetical citation like *(CFPB, 2022)* or
    (Experian, 2023) into an inline markdown hyperlink to the real source.
    Examples:
      *(CFPB, 2022)* → [CFPB (2022)](https://www.consumerfinance.gov/data-research/consumer-credit-trends/)
      *(Experian State of Credit, 2023)* → [Experian State of Credit (2023)](https://www.experian.com/blogs/ask-experian/state-of-credit/)
      *(BLS, 2024)* → [Bureau of Labor Statistics (2024)](https://www.bls.gov/cps/)
      *(Federal Reserve, 2023)* → [Federal Reserve (2023)](https://www.federalreserve.gov/publications/report-on-the-economic-well-being-of-us-households.htm)
    If you are not certain of the exact page URL, link to the main research
    or data page of the authoritative source (consumerfinance.gov,
    bls.gov, federalreserve.gov, experian.com/blogs, etc.).
    NO parenthetical-only citations — every stat must have a clickable link.
13. INSTAGRAM HANDLE: The correct handle is @ivyedge.co. Any reference to
    @joinivyedge or instagram.com/joinivyedge must be changed to
    https://www.instagram.com/ivyedge.co/
15. SHOW DON'T TELL: Replace any sentence that labels an emotion or outcome
    with one that demonstrates it through specifics.
    BAD:  "Many women feel frustrated by the credit system."
    GOOD: "Carmen's business grossed $180k last year. The loan officer asked
           if she had a co-signer."
    BAD:  "This policy disproportionately affects women."
    GOOD: "Lenders require 2 years of W-2 history. The average career break
           for a caregiver is 2.2 years. Do the math."
    If any abstract claims remain, rewrite them with a scene, a number, or a
    consequence that makes the point without having to state it.

16. ANALOGIES: Verify at least one plain-world analogy exists for any abstract
    financial mechanism. If the draft explains a concept abstractly, add or
    strengthen the analogy. It should be conversational — something you'd say
    to a friend, not a textbook.

14. STANDARD FOOTER: Do NOT include the block that reads "Ivy Edge is being
    built for every woman who has been underestimated by a system that never
    genuinely evaluated her. / If that's you, we want you close when we
    launch. / Get on the Ivy Edge waitlist → / Be first. You've waited long
    enough." That block is added automatically by the publisher. End the post
    after the closing CTA paragraph — do not repeat it or duplicate it.

VOICE CALIBRATION
GOOD: "Your 1099 income isn't unstable. Banks are measuring the wrong thing."
BAD:  "Freelance income may present challenges for traditional underwriting."

GOOD: "This is frustrating — let's fix it."
BAD:  "We understand this situation may cause some concern."

OUTPUT
Return ONLY the revised post in clean markdown. No commentary, no diff —
the next phase needs a finished draft to pass to SEO.

{self._voice_block()}

# === Brand context ===
{self._full_brand_context()}
"""
        return self._call_claude(prompt, PHASE_TOKEN_BUDGETS["voice_edit"], "voice_edit")

    # -- Phase 5: SEO -----------------------------------------------------

    def seo_phase(self, brief: ArticleBrief, edited_draft: str) -> dict:
        """Returns a dict with keys: final_draft, meta_description,
        internal_link_suggestions, external_link_suggestions, alt_text_suggestions.
        Asks Claude to return JSON so we can parse cleanly."""

        prompt = f"""You are optimizing this Ivy Edge blog post for SEO.

DRAFT
{edited_draft}

SEO TARGETS
- Primary keyword: {brief.primary_keyword}
- Secondary keywords: {", ".join(brief.secondary_keywords) or "(none)"}

SEO CHECKLIST
1. Integrate the primary keyword in the H1 title, the first 100 words, and at
   least one H2. Use it naturally 3-5 times across the body.
2. Weave secondary keywords in where they fit. Use semantic variations if a
   keyword feels forced.
3. Do NOT add internal links — Ivy Edge has no other published pages or
   blog posts yet. If the draft contains any [anchor text](/blog/...) or
   [anchor text](/products/...) links, remove the link and keep only the
   plain anchor text.
4. REQUIRED — embed 2-4 external source links directly in the draft body:
   - Every statistic, study, or data point cited must be hyperlinked to
     its actual source (government agency, .edu, CFPB, Fed, BLS, AARP,
     peer-reviewed research, or major journalism). Use real URLs.
   - Format: [anchor text describing the source](https://real-url.gov/...)
   - Place the link inline where the stat appears, not in a footnotes section.
   - If the draft cites a stat without a link, add one now. No naked numbers.
5. Write a meta description: <=155 characters, includes primary keyword,
   action-oriented, value-forward.
6. Suggest alt text for any images the editor should add (descriptive +
   keyword where natural).

DO NOT sacrifice Ivy Edge voice for keyword density. If the keyword doesn't
fit naturally, use a semantic variation.

OUTPUT FORMAT — two clearly separated sections, nothing else:

SECTION 1 — the full SEO-optimized post in markdown, between these exact delimiters:
===DRAFT_START===
<your markdown here>
===DRAFT_END===

SECTION 2 — metadata as a single valid JSON object, between these exact delimiters:
===META_START===
{{
  "meta_description": "<= 155 chars",
  "internal_link_suggestions": [
    {{"anchor_text": "...", "url": "/...", "where_in_post": "section name"}}
  ],
  "external_link_suggestions": [
    {{"anchor_text": "...", "url": "https://...", "source": "CFPB/Fed/...", "where_in_post": "..."}}
  ],
  "alt_text_suggestions": [
    {{"image_topic": "...", "alt_text": "..."}}
  ]
}}
===META_END===

{self._voice_block()}

# === Strategy context (SEO + pillars) ===
{self.context.get("strategy", "")}
"""
        raw = self._call_claude(prompt, PHASE_TOKEN_BUDGETS["seo"], "seo")
        return _parse_json_response(raw)

    # -- Phase 6: Social media --------------------------------------------

    @staticmethod
    def _blog_url(topic: str) -> str:
        slug = re.sub(r"[^a-z0-9\s-]", "", topic.lower())
        slug = re.sub(r"\s+", "-", slug).strip("-")[:60]
        return f"https://www.ivyedge.co/blog/{slug}"

    def social_phase(self, brief: ArticleBrief, final_draft: str) -> str:
        post_url = self._blog_url(brief.topic)
        prompt = f"""You are writing social media content to distribute an Ivy Edge blog post.

PRE-LAUNCH CONTEXT
Ivy Edge has not launched any products. Every CTA must be audience-building:
waitlist signup, newsletter, share, survey, or tell-us-your-story.
Never mention Ivy Smart Loan, Ivy Credit Builder, Ivy Credit Monitor, or Ivy Checking.

BLOG POST
Topic: {brief.topic}
Persona: {brief.persona}
Primary keyword: {brief.primary_keyword}
Blog URL: {post_url}

FULL FINAL DRAFT
{final_draft}

{self._voice_block()}

---

Produce all assets below. Follow each format exactly.

---

## X

Write THREE posts. Each will go out on a different day (Tue / Wed / Thu) — they must be
distinct angles, not variations of the same idea.
Rules for every post:
- ≤ 280 characters total (count carefully — hard limit)
- First line: single punchy, opinionated statement or surprising stat — no setup
- Second line (optional): one concrete consequence or reframe
- Separate lines with a BLANK LINE — single newlines are ignored by social platforms
- No hashtags, no em-dashes (—), no URL (link is appended automatically)

Post 1 angle: surprising stat or counterintuitive fact
Post 2 angle: direct challenge to a belief the audience holds
Post 3 angle: relatable frustration — name the exact moment she's lived

Format:
### Post 1
<post text — no URL>

### Post 2
<post text — no URL>

### Post 3
<post text — no URL>

---

## Threads

Write ONE post. Threads rewards a more personal, conversational tone than X.
Rules:
- 150–400 characters
- 2–3 short paragraphs separated by line breaks — feels like a thought unfolding
- Warmer and more personal than X — like sharing something you genuinely believe
- Lead with a specific, relatable moment or observation (not a generic stat)
- End with a question OR a soft CTA — do not include a URL
- No hashtags
- No em-dashes (—); use a dash (-) or a line break instead

Format:
### Post
<post text — no URL>

---

## Instagram

Write THREE captions. Each will post on a different day (Tue / Thu / Sat) with a different
hook and angle — they must feel distinct, not like rewrites of each other.
Rules for every caption:
- 150–300 words; warm, direct Ivy Edge voice; line breaks every 1–2 sentences
- Hook in the first line (no "Hey!" or emojis to open)
- 3–5 paragraphs; end with a question or CTA to drive comments; include "link in bio"
- Do NOT include raw URLs in the caption body
- Hashtags: 10–15 highly relevant tags on a separate line after each caption

Caption 1 angle: lead with the core problem or stat
Caption 2 angle: lead with a personal story or relatable moment
Caption 3 angle: lead with a quick win or actionable tip

Also include one shared Visual direction note describing the image style for all three.

Format:
### Caption 1
<caption text>

### Hashtags 1
<hashtags>

### Caption 2
<caption text>

### Hashtags 2
<hashtags>

### Caption 3
<caption text>

### Hashtags 3
<hashtags>

### Visual direction
<1–2 sentences describing the image — Ivy Edge brand colors only>

---

## TikTok / Reels

Write TWO scripts. Each is a separate 30-second video — different hook, different angle.
Script 1 leads with a stat or counterintuitive truth.
Script 2 leads with a relatable frustration or direct challenge.

Rules for every script:
- Length: 30 seconds — roughly 60–75 words total (phrases flash on screen)
- Body: 3–4 tight sentences of genuine insight or concrete facts
- CTA: one closing sentence ending with "ivyedge.co"
- Plain text ONLY — no [TEXT:] or [VISUAL:] cues, no headers, no bullets
- No filler words, no "so basically"
- Confident, direct, warm — like texting a smart friend the most important thing

For each script, first write 5 hook options (one per technique), then write the script.

Hook techniques:
  1. Surprising stat     2. Direct challenge     3. Relatable frustration
  4. Counterintuitive truth     5. Bold accusation

Format:
### Hook options 1
1. (stat) ...
2. (challenge) ...
3. (frustration) ...
4. (truth) ...
5. (accusation) ...
**Recommended:** #N — [one sentence why]

### Script 1
<plain text — recommended hook, body, CTA>

### Hook options 2
1. (stat) ...
2. (challenge) ...
3. (frustration) ...
4. (truth) ...
5. (accusation) ...
**Recommended:** #N — [one sentence why]

### Script 2
<plain text — recommended hook, body, CTA>

---

## Stories

Write 4 Story slides. Each is a single burst of text displayed full-screen on a 9:16 card.
Rules:
- Max 12 words per slide — read in under 3 seconds
- No hashtags, no URLs, no em-dashes
- Each slide must stand completely alone

Slide 1 (stat): the most surprising number or fact from the article
Slide 2 (quote): the most quotable single sentence — put it in "quotes"
Slide 3 (CTA): one clear action (e.g. "The full breakdown is in the link in bio")
Slide 4 (question): an open question the audience will want to answer

Format:
### Slide 1
<text>

### Slide 2
<text>

### Slide 3
<text>

### Slide 4
<text>

---

## Reddit

Write a link post for Reddit personal finance communities.
Rules:
- Title: specific, factual, no clickbait — Reddit rewards useful titles
  (e.g. "Why your credit score dropped during your career gap — even with zero missed payments")
- Body: 2–4 sentences of genuine value. Share the key insight from the article.
  End with one sentence: "Full breakdown: [article URL]" — use the actual blog URL ({post_url})
- No promotional language, no "check out my article" — lead with the insight
- Sound like a knowledgeable community member sharing something useful

Format:
### Reddit Title
<title text>

### Reddit Body
<body text with article link at end>

---

## LinkedIn Article

Write a full LinkedIn native article for the Ivy Edge business page.
LinkedIn articles live on the page permanently and index on Google — write for both audiences.
Rules:
- Headline: specific and benefit-driven, 8–12 words, no clickbait
- Length: 500–800 words total
- Structure: 4–6 short sections with bold headers (no # markdown — use **Bold Header**)
- Voice: warm, authoritative, and direct — like a founder who knows her stuff and wants to help
- Open with the core problem in 2–3 sentences — no preamble, no "In today's world..."
- Each section: 2–4 sentences max, no filler
- Include 1–2 real stats or concrete facts from the article
- End with a 2-sentence CTA: one sentence naming what Ivy Edge is building, one sentence sending readers to the waitlist at https://www.ivyedge.co
- No hashtags in the body — add 3–5 relevant hashtags on a separate line at the very end
- No em-dashes (—); use a colon or a dash (-)

Format:
### Headline
<headline text>

### Body
<full article body — plain text with **Bold Headers** for sections, blank lines between paragraphs>

### Hashtags
<3–5 hashtags on one line>
"""
        return self._call_claude(prompt, PHASE_TOKEN_BUDGETS["social"], "social")

    # -- Barbie content brief ---------------------------------------------

    def barbie_phase(self, brief: ArticleBrief, final_draft: str) -> str:
        """Generate a weekly Barbie content brief for Audrey's daughters to film."""
        prompt = f"""You are writing a content brief for two teenage girls (ages 12 and 16)
who film short videos with their cat, Barbie. Barbie wears an Ivy Edge branded bandana as her signature look.
The videos and photos support Ivy Edge, a financial education brand for freelancers and gig workers.

CRITICAL — BARBIE SPEAKS IN FIRST PERSON:
All voiceover scripts are written FROM BARBIE'S PERSPECTIVE. Barbie is the character.
The girls are just her voice. Use "I", "me", "my mom", "my house", "I live here" — never
"our mom" or anything that breaks the illusion. Barbie is a confident, slightly unimpressed
cat who understands money better than most humans and is not afraid to say so.
She is funny, direct, and always right.

NEVER mention the mom's name — she is always just "my mom." No names, ever.

The girls do NOT need to understand the financial topic deeply — they just need clear,
fun, doable directions. Write like a cool older sister giving instructions, not a brand manager.

THE PERMANENT STUDIO SETUP (never changes — the girls know this already)
- Forest green backdrop wall in the home office
- Barbie always sits on her throne: a small wooden crate/rattan basket in the center
- Large pothos or fiddle leaf plant to the left
- Small succulent near Barbie
- Stack of 2–3 dark hardcover books
- Coral or cream candle
- Small chalkboard or wooden sign ("Ivy Edge" or "Grow Through Anything")
- Neutral mug
- Ring light for videos, natural window light for photos

The studio bones NEVER change. Only ONE small prop swaps each week to keep it fresh
(the "This Week's Swap" — you generate this below).

THIS WEEK'S ARTICLE
Topic: {brief.topic}
Core insight (in plain English): use the article to find the one most surprising or
relatable takeaway a teenager could explain — write it in one sentence here before
starting the brief.

THE ARTICLE (for reference)
{final_draft[:3000]}

---

Produce the full Barbie Content Brief below. Follow each format exactly.

---

## This Week's Angle
One sentence — the core idea from the article translated into something a teenager
could explain while holding a cat. Keep it fun and simple.

---

## This Week's Swap
ONE small prop change to make this week's content feel fresh — tied to the article topic.
Examples: swap the mug for one with a relevant phrase, add a seasonal item, change what's
written on the chalkboard, give Barbie a tiny accessory beyond her ivy hat.

**Swap:** [exactly what to change and what to replace it with]
**Why it works:** [one sentence — how it connects to this week's topic]

---

## TikTok / Reels — 3 Video Ideas

For each video: Barbie's specific action + the voiceover script + a caption starter.
The studio setup is fixed — do NOT describe the background, lighting, or permanent props.
Only describe what BARBIE does and any non-studio items held in frame.

Rules:
- Voiceover: written for a 16-year-old to read naturally — conversational, not stiff
- Each script: 40–60 words (about 20–25 seconds with Barbie on screen)
- Give Barbie one specific, fun action per video (e.g. "hold Barbie up facing the camera
  like she's presenting", "drape Barbie across the open book like she's reading it")
- End every script with: "Follow Ivy Edge for more — link in bio"
- Caption starter: first 1–2 lines only — the girls fill in the rest
- No financial jargon — translate everything into plain, relatable language

Format for each:

### Video [N]: [fun title]

**Barbie's action:** [one sentence — exactly what to do with the cat]

**Voiceover script:**
[script text — exactly as the girl should say it out loud]

**Caption starter:**
[first 1–2 lines]

**Hashtags:** [8–10 tags relevant to cat content + personal finance + the topic]

---

## Instagram Feed — 2 Photo Ideas

Same studio, same rules — describe only Barbie's pose/action and any held props.

Rules:
- Barbie wears the ivy hat in both
- Caption: 80–150 words, warm and a little funny — sounds like it could come from the girls
- Include "link in bio" reference and 10–12 hashtags at the bottom

Format for each:

### Photo [N]: [fun title]

**Barbie's pose:** [one sentence — exactly how to position the cat]
**Hold in frame:** [any non-studio item to add, or "nothing extra this week"]

**Caption:**
[full ready-to-post caption + hashtags]

---

## Quick Tips for the Girls
3–4 bullet points of practical filming advice for this specific week's content
(lighting, angles, how to get Barbie to cooperate, etc.)

---

## This Week's Posting Schedule

Write this as a simple checklist the girls can check off. Use this exact structure:

| Done | What | Where | When |
|------|------|--------|------|
| ☐ | Photo 1: [title] | Instagram feed | Tuesday |
| ☐ | Video 1: [title] | TikTok + Reels | Wednesday |
| ☐ | Video 2: [title] | TikTok + Reels | Friday |
| ☐ | Photo 2: [title] | Instagram feed | Saturday |
| ☐ | Video 3: [title] | TikTok + Reels | Sunday |

Add one line below the table:
"Film anytime — just try to post on the days above for the best reach!"
"""
        return self._call_claude(prompt, 4000, "barbie")

    # -- Full pipeline ----------------------------------------------------

    def generate_blog_post(
        self,
        topic: str,
        persona: str,
        pillar: str,
        keywords: Iterable[str],
        content_format: str = "educational",
        notes: str = "",
        target_word_count: tuple[int, int] = (1400, 1600),
        on_phase: Optional[callable] = None,
    ) -> GenerationResult:
        """Run all five phases and return the assembled result.

        on_phase: optional callback fired with (phase_name, result_text) so
        callers can stream progress to a UI/log/Slack.
        """
        keywords = list(keywords)
        if not keywords:
            raise ValueError("At least one keyword is required (the primary).")

        brief = ArticleBrief(
            topic=topic,
            persona=persona,
            pillar=pillar,
            primary_keyword=keywords[0],
            secondary_keywords=keywords[1:],
            content_format=content_format,
            target_word_count=target_word_count,
            notes=notes,
        )

        result = GenerationResult(
            brief=brief,
            model=self.model,
            started_at=datetime.utcnow().isoformat() + "Z",
        )

        def step(name: str, fn):
            logger.info("---- Phase: %s ----", name)
            out = fn()
            if on_phase:
                on_phase(name, out)
            return out

        # Phase 0 — competitive format analysis (non-fatal if it fails)
        try:
            logger.info("---- Phase: format_analysis ----")
            _, guidance = run_competitor_analysis(brief.primary_keyword)
            result.format_analysis = guidance
            if on_phase:
                on_phase("format_analysis", guidance)
        except Exception as e:
            logger.warning("Format analysis skipped: %s", e)
            result.format_analysis = ""

        result.research = step("research", lambda: self.research_phase(brief, result.format_analysis))
        result.outline = step("outline", lambda: self.outline_phase(
            brief, result.research, result.format_analysis
        ))
        result.first_draft = step("draft", lambda: self.draft_phase(brief, result.outline))
        result.edited_draft = step("voice_edit", lambda: self.voice_edit_phase(result.first_draft))

        seo_out = step("seo", lambda: self.seo_phase(brief, result.edited_draft))
        result.final_draft = seo_out.get("final_draft", result.edited_draft)
        result.meta_description = seo_out.get("meta_description", "")

        result.social = step("social", lambda: self.social_phase(brief, result.final_draft))
        result.barbie = step("barbie", lambda: self.barbie_phase(brief, result.final_draft))

        result.token_usage = {
            **self._cumulative_usage,
            "internal_link_suggestions": seo_out.get("internal_link_suggestions", []),
            "external_link_suggestions": seo_out.get("external_link_suggestions", []),
            "alt_text_suggestions": seo_out.get("alt_text_suggestions", []),
        }
        result.finished_at = datetime.utcnow().isoformat() + "Z"
        return result

    # -- Intro post (one-time founding statement) --------------------------

    def generate_intro_post(self, on_phase: Optional[callable] = None) -> GenerationResult:
        """Generate the Ivy Edge founding/introduction post.

        This is a one-time brand story piece — shorter than a standard post,
        no keyword optimization, written as a direct letter to the reader.
        """
        brief = ArticleBrief(
            topic="Introducing Ivy Edge",
            persona="All",
            pillar="Brand Story",
            primary_keyword="Ivy Edge",
            content_format="brand_introduction",
            target_word_count=(700, 900),
            notes="Founding statement. Not keyword-optimized. Warm, personal, direct. Waitlist CTA.",
        )

        result = GenerationResult(
            brief=brief,
            model=self.model,
            started_at=datetime.utcnow().isoformat() + "Z",
        )

        def step(name: str, fn):
            logger.info("---- Phase: %s ----", name)
            out = fn()
            if on_phase:
                on_phase(name, out)
            return out

        prompt = f"""You are writing the founding/introduction post for Ivy Edge — the very first thing
the world reads from us. This is not a blog post. It is a letter.

WHO WE ARE WRITING TO
All four of our personas at once: Priya (the career returner), Maya (the freelancer),
Carmen (the established entrepreneur), Dominique (the corporate climber). Each of them
has been doing everything right and still can't get a fair shot from traditional finance.

WHAT THIS POST MUST DO
1. Open with the problem — not our solution. The reader should feel seen before we say a word about ourselves.
2. Explain why the financial system fails these women (income type, career path, the metrics it uses).
3. Introduce Ivy Edge — what we're building and why. One sentence on the mission.
4. Tell the reader what's coming: a blog that gives them the real information they've been denied,
   and products (launching soon) that evaluate their whole story.
5. End with a warm, direct CTA to join the waitlist at https://www.ivyedge.co.

WHAT TO AVOID
- No corporate language. No "we're excited to announce." No "we're on a mission to."
- No product names — we haven't launched yet.
- Do not over-promise on the products. Say they're coming. Don't describe features.
- Do not write a listicle. This is prose.

TONE
The brilliant friend who happens to work in finance — the one you actually call.
She's been watching the system fail people she cares about and she's done being polite about it.
Warm. Direct. A little frustrated. Deeply hopeful.

TARGET LENGTH
700–900 words. No filler. Every sentence earns its place.

FORMAT
Return clean markdown only:
- H1 title (direct, human — not a tagline)
- 4–6 prose paragraphs
- A short, warm sign-off before the CTA
- CTA paragraph

{self._voice_block()}

# === Brand context ===
{self._full_brand_context()}
"""
        result.final_draft = step("intro", lambda: self._call_claude(
            prompt, 2000, "intro"
        ))

        # Social for the intro post
        result.social = step("social", lambda: self.social_phase(brief, result.final_draft))
        result.finished_at = datetime.utcnow().isoformat() + "Z"
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_json_response(raw: str) -> dict:
    """Parse the SEO phase response, which uses delimited sections to keep
    the markdown draft separate from the JSON metadata."""
    draft = ""
    meta: dict = {}

    if "===DRAFT_START===" in raw and "===DRAFT_END===" in raw:
        draft = raw.split("===DRAFT_START===", 1)[1].split("===DRAFT_END===", 1)[0].strip()

    if "===META_START===" in raw and "===META_END===" in raw:
        meta_txt = raw.split("===META_START===", 1)[1].split("===META_END===", 1)[0].strip()
        # Strip any ```json fence the model might add
        if meta_txt.startswith("```"):
            meta_txt = meta_txt.split("\n", 1)[1] if "\n" in meta_txt else meta_txt
            if meta_txt.rstrip().endswith("```"):
                meta_txt = meta_txt.rstrip()[:-3]
        try:
            meta = json.loads(meta_txt)
        except json.JSONDecodeError as e:
            logger.warning("SEO metadata JSON invalid: %s", e)

    if not draft and not meta:
        logger.warning("SEO phase returned unexpected format — using raw output as draft")
        draft = raw

    return {
        "final_draft": draft or raw,
        "meta_description": meta.get("meta_description", ""),
        "internal_link_suggestions": meta.get("internal_link_suggestions", []),
        "external_link_suggestions": meta.get("external_link_suggestions", []),
        "alt_text_suggestions": meta.get("alt_text_suggestions", []),
    }
