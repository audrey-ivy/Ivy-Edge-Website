# Research summaries

Drop one markdown file per research source in this folder. The agent will load every `.md` here automatically and inject it as additional context.

Suggested files (placeholders — replace with your real summaries):

- `mintel_ai_banking_2026.md` — Mintel report on AI as the new front door to banking; cite the 49% ChatGPT/Gemini stat
- `banking_experience_us_2025.md` — Mintel banking-experience study; include junk-fee data, overdraft rules
- `behavioral_science_references.md` — Key papers behind Pillar 4 mechanics (commitment devices, endowed progress, goal gradient, social identity)
- `cfpb_junk_fees_2025.md` — CFPB final rule on overdraft fees, $8B context, 75% reduction figure
- `fico_history_and_assumptions.md` — Origin of FICO, 1989 design assumptions, what it doesn't measure
- `women_credit_access_data.md` — 41% credit-decline statistic, $1.7T unmet lending demand, gender gap research

## Format

Each summary should be ~300–800 words and include:

```
# {Source title}

**Source:** {Publication / org / authors, year}
**URL:** {if available}

## Key claims to cite
- ...

## Numbers we can use
- ... (verify before publishing)

## Quotes (short, attributable)
- "..."

## How it connects to IvyEdge
- ...
```

Keep these tight — the more sources you load, the longer your prompt, and you'll start hitting context limits or just paying for tokens you don't need.
