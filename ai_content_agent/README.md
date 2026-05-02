# IvyEdge AI Content Agent

A five-phase content pipeline that turns an editorial brief into a publishable IvyEdge blog draft, while preserving the brand voice from `IvyEdge_Brand_Guidelines.docx`.

> **Pre-launch mode.** IvyEdge has not launched any products yet, so the agent is configured for **demand validation**, not product acquisition. Posts establish authority on the topic, build an email list, and collect demand signal (waitlist signups, story submissions, survey responses). No post will reference Ivy Smart Loan / Ivy Credit Builder / Ivy Credit Monitor / Ivy Checking — those products live inside `context/product_knowledge.md` so the agent understands the thesis, but every CTA the agent writes is an audience-building action (waitlist, newsletter, share, survey, tell-us-your-story). When IvyEdge launches, swap the CTA bank in `content_strategy.md` and remove the `PRE-LAUNCH CONTEXT` blocks in `ivyedge_content_agent.py` to flip into product mode.

```
┌────────────────────────────┐
│  Layer 1: Context library  │  brand_voice / personas / product_knowledge / strategy
└──────────────┬─────────────┘
               ↓
┌────────────────────────────┐
│  Layer 2: Generation engine │  Research → Outline → Draft → Voice edit → SEO
└──────────────┬─────────────┘
               ↓
┌────────────────────────────┐
│  Layer 3: Human QC + CMS   │  Editor review (15–20 min) → schedule → publish
└────────────────────────────┘
```

## Folder layout

```
ai_content_agent/
├── ivyedge_content_agent.py     # Main agent class (the engine)
├── run_pipeline.py              # CLI: single post or batch from CSV
├── requirements.txt
├── .env.example                 # Copy to .env, fill in your key
├── .gitignore
├── editorial_calendar.csv       # Sample calendar — first 8 weeks of 2026
├── context/
│   ├── brand_voice.md           # Voice principles, vocab, tone calibration
│   ├── personas.md              # Priya, Maya, Carmen, Dominique
│   ├── product_knowledge.md     # Ivy Smart Loan, Credit Builder, etc.
│   ├── content_strategy.md      # 5 pillars, SEO, cadence, KPIs
│   ├── research_summaries/      # Drop research .md files here
│   └── examples/                # Drop example articles here (huge quality lift)
└── output/                      # Drafts land here (gitignored)
```

## Setup (one time)

```bash
cd ai_content_agent

# 1. Install dependencies
python3 -m venv .venv
source .venv/bin/activate           # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Add your API key
cp .env.example .env
# then open .env and paste your ANTHROPIC_API_KEY

# 3. (Highly recommended) Drop 2–3 finished IvyEdge posts into context/examples/
#    The agent's voice quality depends heavily on having real on-brand examples.
```

## Generate one post

```bash
python run_pipeline.py single \
  --topic "How freelancers can prove income stability" \
  --persona Maya \
  --pillar "Pillar 1: Financial Education for Non-Traditional Paths" \
  --keywords "freelance income proof,1099 loan approval,freelancer credit"
```

Output lands in `output/2026-05-01_how-freelancers-can-prove-income-stability/`:

```
00_brief.json           # The editorial brief
01_research.md          # Phase 1 — research + IvyEdge angle
02_outline.md           # Phase 2 — structured outline
03_first_draft.md       # Phase 3 — first draft
04_edited_draft.md      # Phase 4 — voice-tightened version
05_final_draft.md       # Phase 5 — SEO-optimized FINAL ← send this to editor
meta.json               # Meta description, internal/external link suggestions, alt text, tokens
```

## Generate a week's worth (batch mode)

```bash
python run_pipeline.py batch --calendar editorial_calendar.csv
```

Reads every row where `status=queued`, generates the post, drops the artifacts in `output/`, and updates the row to `status=drafted` with the path. Then the editor reviews each `05_final_draft.md` against the [quality control gates](#quality-control-gates).

## Quality control gates

The agent does not skip the human review step. After the run, the editor reads `05_final_draft.md` and checks:

| Gate | What to look for | Time |
| --- | --- | --- |
| Factual accuracy | Stats cited correctly; product features described accurately; external links real | ~5 min |
| Compliance & legal | No "guaranteed approval" or "transform"; required disclaimers present; CFPB-safe | ~3 min |
| Brand authenticity | Sounds like IvyEdge; CTA tied to right product; persona-fit | ~5 min |
| Customer-story accuracy | Only Pillar 3 — member must have approved use of their story | ~10 min |

The pipeline can take a draft to 90%. The last 10% (and all factual sign-off) stays human.

## Editorial calendar columns

| Column | Required | Notes |
| --- | --- | --- |
| `publish_date` | yes | YYYY-MM-DD |
| `title` | yes | Working title — agent may suggest alternates |
| `persona` | yes | `Priya` / `Maya` / `Carmen` / `Dominique` / `All` |
| `pillar` | yes | Use the full pillar name from `content_strategy.md` |
| `primary_keyword` | yes | Single most important keyword |
| `secondary_keywords` | no | Pipe-separated: `kw 1\|kw 2\|kw 3` |
| `format` | no | `educational` / `customer_story` / `behavioral` / `industry` |
| `status` | yes | `queued` → `drafted` → (your team) `approved` / `published` |
| `notes` | no | Editor's instructions for that specific post |

## Choosing a model

The default is `claude-sonnet-4-6` (set in `.env` or `IVYEDGE_MODEL`). For faster/cheaper drafts of low-stakes posts, point at `claude-haiku-4-5-20251001`. For your highest-stakes thought-leadership pieces (Pillar 5), use `claude-opus-4-6`.

## Costs (rough)

Per post, all five phases combined:

- Sonnet 4.6: ~$0.10–0.20 (depending on context library size)
- Opus 4.6: ~$0.40–0.80
- Haiku 4.5: ~$0.02–0.05

For 12 posts/month with Sonnet: ~$1.20–2.40/month. Editor time saved: ~30 hours.

## Programmatic use

```python
from ivyedge_content_agent import IvyEdgeContentAgent

agent = IvyEdgeContentAgent()  # picks up ANTHROPIC_API_KEY from env

result = agent.generate_blog_post(
    topic="How freelancers can prove income stability",
    persona="Maya",
    pillar="Pillar 1: Financial Education for Non-Traditional Paths",
    keywords=["freelance income proof", "1099 loan approval", "freelancer credit"],
    on_phase=lambda name, _: print(f"[{name}] done"),
)

print(result.final_draft)
print(result.meta_description)
```

## Tuning the voice over time

The single biggest quality lever is the `context/examples/` folder. Every time the editor revises a draft, save the before/after as a new example. The agent's voice tightens with every iteration.

The second lever is `context/research_summaries/` — drop a 500-word summary for each Mintel report, CFPB ruling, or peer-reviewed paper you cite often. The agent will pull from it whenever the topic is relevant.

If you find the agent drifting on a specific phrase, add the offending phrase to the "Words we avoid" list in `brand_voice.md` and call `agent.reload_context()`.

## What stays human

Per the strategy doc, never automate end-to-end:

- **Strategic decisions:** which topics to cover, when to publish
- **Original research:** interviews, surveys, member quotes
- **Brand evolution:** updating the voice or vocabulary
- **Crisis response:** anything reactive to industry events
- **Customer-story sign-off:** explicit member approval before any Pillar 3 post ships
- **Final legal review**

## Roadmap (next things to wire up)

- WordPress / Webflow API publish step (today the editor copy-pastes from `05_final_draft.md`)
- Slack notifier (post `"3 drafts ready for review"` to your editorial channel)
- Per-post diff between `04_edited_draft.md` and `05_final_draft.md` so editors can see what SEO changed
- A/B variant generation (run draft phase twice with different angles, pick the stronger)
