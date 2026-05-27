# Ivy Edge AI Content Agent

An end-to-end content pipeline: researches, writes, and schedules Ivy Edge blog posts and social media automatically — with Audrey reviewing and publishing on Substack manually.

> **Pre-launch mode.** Every post builds the waitlist and audience, not product acquisition. CTAs are always: waitlist signup, newsletter, share, or story submission. No post names Ivy products directly. When Ivy Edge launches, update the CTA bank in `context/content_strategy.md`.

---

## What runs automatically vs. what you do

| Step | Who | When |
|---|---|---|
| Extend editorial calendar 12 weeks | Agent | Every Tuesday (run_monday.sh) |
| Inject trending topics into queued posts | Agent | Every Tuesday |
| Generate drafts for posts due within 7 days | Agent | Every Tuesday |
| Validate and strip dead links from every draft | Agent | At generation time |
| Save draft to Substack (NOT published) | Agent | Every Tuesday if `--publish` flag set |
| Review draft in Substack editor | **Audrey** | After Tuesday run |
| Hit publish in Substack | **Audrey** | When ready |
| Generate branded image card (1080×1080) | Agent | Automatically after generation |
| Schedule image card to Instagram + Threads + X | Agent | Wednesday noon UTC |
| Schedule video (when available) to Instagram + Threads + TikTok + X | Agent | Thursday noon UTC |

---

## Architecture

```
run_monday.sh  (runs every Tuesday)
    │
    ├─ 1. calendar_agent.py       — extend + inject trending topics
    │
    ├─ 2. run_pipeline.py batch   — generate drafts for posts due this week
    │       │
    │       ├─ ivyedge_content_agent.py   — 5-phase writing pipeline
    │       │     Research → Outline → Draft → Voice edit → SEO
    │       │
    │       ├─ Link validator              — checks every URL; strips dead ones
    │       │
    │       └─ substack_publisher.py      — saves as DRAFT (does not publish)
    │
    └─ 3. social_media_agent.py   — image cards + Buffer scheduling
            │
            ├─ image_card_generator.py    — 1080×1080 branded PNG
            ├─ video_generator.py         — TikTok/Reels MP4 (requires ivy_background.mp4)
            └─ buffer_poster.py           — uploads to Cloudinary → schedules in Buffer
```

---

## Folder layout

```
ai_content_agent/
├── run_monday.sh                # Cron entry point — runs the whole pipeline
├── run_pipeline.py              # CLI: intro | single | batch modes
├── ivyedge_content_agent.py     # Core writing agent (5 phases)
├── substack_publisher.py        # Creates Substack DRAFTS (no auto-publish)
├── social_media_agent.py        # Image cards + Buffer scheduling
├── image_card_generator.py      # Branded 1080×1080 PNG generator
├── video_generator.py           # TikTok/Reels MP4 generator
├── buffer_poster.py             # Buffer GraphQL API — Instagram, Threads, TikTok, X
├── calendar_agent.py            # Extends editorial calendar + injects trends
├── trend_monitor.py             # Fetches arXiv / news trends
├── editorial_calendar.csv       # Source of truth for all content
├── .env                         # API keys (never commit)
├── requirements.txt
├── assets/
│   ├── ivy_background.mp4       # Required for video generation
│   └── background_music.mp3     # Required for video generation
├── context/
│   ├── brand_voice.md
│   ├── personas.md              # Priya, Maya, Carmen, Dominique
│   ├── product_knowledge.md
│   └── content_strategy.md
└── output/                      # Generated drafts (one folder per post)
    └── YYYY-MM-DD_post-slug/
        ├── 00_brief.json
        ├── 01_research.md
        ├── 02_outline.md
        ├── 03_first_draft.md
        ├── 04_edited_draft.md
        ├── 05_final_draft.md    ← what goes to Substack
        ├── 06_social.md         ← Instagram / Threads / TikTok / X / Reddit copy
        ├── 07_image_card.png    ← auto-generated branded image
        ├── meta.json
        └── social_posted.json   ← receipt; prevents double-posting
```

---

## Editorial calendar statuses

| Status | Meaning |
|---|---|
| `scheduled` | Planned — not yet ready to generate (publish date > 7 days away) |
| `queued` | Ready to generate — pipeline will pick this up on the next Tuesday run |
| `drafted` | Draft generated locally; not yet published to Substack |
| `published` | Live on Substack (Audrey hit publish manually) |

**The pipeline only generates posts where `status=queued` AND `publish_date` is within the next 7 days.** Change a `scheduled` row to `queued` when you want it drafted on the next run.

---

## Substack workflow (draft-only)

The agent **never auto-publishes.** It creates a draft and returns the editor URL:

```
https://joinivyedge.substack.com/publish/post/{draft_id}
```

Audrey opens that link, reviews the draft (15–20 min QC), and hits **Publish** when satisfied.

To update an already-published post programmatically:
```python
from substack_publisher import SubstackPublisher
pub = SubstackPublisher()
pub.update_post(post_id=197225099, body_markdown=open("05_final_draft.md").read())
```
This uses `PUT /api/v1/drafts/{id}` — the correct endpoint for editing published posts.

---

## Social media schedule

Posts schedule automatically once a draft folder has `06_social.md` and no `social_posted.json` receipt.

| Platform | Content | Day | Time |
|---|---|---|---|
| Instagram | Branded image card | Wednesday | 12:00 UTC |
| Threads | Image card + caption | Wednesday | 12:00 UTC |
| X (Twitter) | ≤280-char hook | Wednesday | 12:00 UTC |
| Instagram | Video (Reels) | Thursday | 12:00 UTC |
| Threads | Video | Thursday | 12:00 UTC |
| TikTok | Video | Thursday | 12:00 UTC |
| X (Twitter) | Video | Thursday | 12:00 UTC |

Videos require `assets/ivy_background.mp4` and `assets/background_music.mp3`. Without them, videos are skipped and only image cards are posted.

To prevent double-posting, each folder gets a `social_posted.json` receipt after the first run. Delete the receipt to re-run social for a folder.

---

## Link validation

Every URL in every generated draft is checked before saving. Links that return 4xx errors have their hyperlink syntax stripped (anchor text is preserved). Known bot-blocking domains (government and academic sites that return 403 but work fine in browsers) are whitelisted and left alone:

```
consumerfinance.gov, bls.gov, urban.org, academic.oup.com,
jstor.org, census.gov, dol.gov, cfpb.gov
```

---

## Running manually

```bash
cd ai_content_agent
source .venv/bin/activate

# Generate one post (saves to output/ and creates Substack draft)
python run_pipeline.py --publish single \
  --topic "How freelancers can prove income stability" \
  --persona Maya \
  --pillar "Pillar 1: Financial Education for Non-Traditional Paths" \
  --keywords "freelance income proof,1099 loan approval,freelancer credit"

# Generate all queued posts due this week
python run_pipeline.py --publish batch --calendar editorial_calendar.csv

# Run social media agent on all unposted folders
python social_media_agent.py

# Run social for one specific folder only
python social_media_agent.py --folder output/2026-05-11_rto-mandates-have-a-gender-problem-heres-what-the-data-shows

# Generate image cards only (no Buffer posting)
python social_media_agent.py --cards-only

# Generate everything but don't post to Buffer
python social_media_agent.py --no-post
```

---

## Quality control gates (human review before publishing)

| Gate | What to check | Time |
|---|---|---|
| Factual accuracy | Stats cited correctly; all links resolve; no hallucinated URLs | ~5 min |
| Compliance | No "guaranteed approval" / "transform"; no named Ivy products; CTA correct | ~3 min |
| Brand voice | Sounds like Ivy Edge; persona-appropriate; no generic finance-blog phrases | ~5 min |
| Links | Click every hyperlink in the Substack editor before hitting publish | ~2 min |

---

## Required API keys (.env)

| Key | Used for |
|---|---|
| `ANTHROPIC_API_KEY` | Content generation (Claude) |
| `SUBSTACK_SID` | Saving Substack drafts (session cookie — expires; refresh from browser DevTools) |
| `BUFFER_API_KEY` | Scheduling to Instagram, Threads, TikTok, X |
| `BUFFER_ORG_ID` | Buffer organization ID |
| `BUFFER_IG_CHANNEL_ID` | Instagram channel |
| `BUFFER_THREADS_CHANNEL_ID` | Threads channel |
| `BUFFER_TIKTOK_CHANNEL_ID` | TikTok channel |
| `BUFFER_X_CHANNEL_ID` | X (Twitter) channel |
| `CLOUDINARY_CLOUD_NAME` | Image/video hosting for Buffer |
| `CLOUDINARY_API_KEY` | Cloudinary upload |
| `CLOUDINARY_API_SECRET` | Cloudinary upload |
| `ELEVENLABS_API_KEY` | Voiceover for videos (optional — videos skipped if not set) |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice |
| `REDDIT_CLIENT_ID` | Reddit posting (not yet configured) |
| `REDDIT_CLIENT_SECRET` | Reddit posting (not yet configured) |
| `REDDIT_USERNAME` | JoinIvy Edge |
| `REDDIT_PASSWORD` | Reddit posting (not yet configured) |

---

## What stays human — always

- **Which posts get published** — Audrey reviews every draft and hits publish manually
- **Strategic decisions** — which topics, when, what angle
- **Original reporting** — interviews, member quotes, surveys
- **Brand evolution** — updating voice, vocabulary, content strategy
- **Legal review** — any post making regulatory or compliance claims
- **Customer story sign-off** — explicit member approval before any Pillar 3 post ships
