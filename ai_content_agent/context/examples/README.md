# Example articles

This folder is one of the most important parts of the context library. Showing the agent examples of on-brand IvyEdge writing is more effective than telling it what good looks like.

## What to include

Drop 2–4 markdown files here:

- `good_article_1.md` — A finished, approved post. Voice is dialed. Use one from Pillar 1 or 2.
- `good_article_2.md` — A second example with a different format (e.g., a customer story or a behavioral piece).
- `bad_article_edited.md` — A "before/after": draft that was off-brand on the left, edited version on the right. Mark each correction with `// EDIT: <reason>` so the agent can see *why* something changed.

## Format suggestion for `bad_article_edited.md`

```
# {Title}

## ❌ Before

> Freelance income may present challenges for traditional underwriting models, leveraging
> our innovative platform...

## ✅ After

> Your 1099 income isn't unstable. Banks are measuring the wrong thing.

## What changed and why

- Removed "leverage" — banned vocab
- Killed hedging ("may present challenges")
- Direct hook instead of buried lede
- "Your" instead of "freelance income" — talk to the reader, not about them
```

## Why this matters

Single-shot prompts that say "write like IvyEdge" produce generic output. Examples are how the model actually learns the difference between on-brand and off-brand. Keep refreshing this folder — every editor revision is a free training example for the agent.
