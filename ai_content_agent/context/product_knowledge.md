# IvyEdge — What We're Building (Pre-Launch)

> ⚠️ **CRITICAL EDITORIAL RULE: We are pre-launch. Do NOT reference any IvyEdge products by name in published blog content.** This file documents the thesis and the products we're going to build, so the agent and the editor understand the perspective behind the writing — but none of these products exist yet. Posts must drive **audience-building actions**, not product applications.

---

## Why this file exists

The agent needs to understand IvyEdge's point of view (what problems we're going to solve, how we think about underwriting, what we believe about women's financial access) to write thought-leadership content with conviction. But none of that translates to "apply for X" CTAs in a post. Right now, the blog's job is to prove there's an audience for the thesis.

---

## The thesis (use this in writing — without naming products)

The financial system was designed for salaried, W-2, uninterrupted careers. That's not most people, and it's not most women. We believe:

- **Career gaps shouldn't penalize the people taking them.** Caregiving, sabbaticals, parental leave — these are signals about a life, not a credit risk.
- **1099 income is real income.** Pattern is a stronger signal than payroll structure.
- **Profitability is profitability.** Five years of business history is an arbitrary threshold, not a probationary period.
- **Doing everything right shouldn't get you a worse deal.** High earners with good credit deserve products that match their ambition, not generic offerings priced like a punishment.
- **Plain-language transparency isn't a feature. It's a baseline.**

**You can put any of those statements in a post.** That's the thesis. What you can't do is follow it with "and here's the product we built to fix it" — because we haven't built it yet.

---

## Products we're building (FOR INTERNAL CONTEXT ONLY — not for blog content)

The following products are in development. Use them to inform the agent's perspective on a topic. Never name them in a published post.

### Ivy Smart Loan (planned)
A personal/business loan that evaluates the full financial picture rather than only credit score and W-2 history. Backed by ZestAI holistic underwriting, designed to weight income trajectory and life context. **Use this internally to shape the agent's view on underwriting topics.**

### Ivy Credit Builder Card (planned)
A secured credit-building card that reports to all three bureaus monthly. **Use this internally to shape the agent's view on credit-building topics.**

### Ivy Credit Monitor (planned)
Free credit-score and trajectory monitoring with plain-language explanations. **Use this internally to shape the agent's view on credit-score transparency topics.**

### Ivy Checking (planned)
A checking account designed for variable-income earners. **Use this internally to shape the agent's view on banking topics.**

### Behavioral mechanics (planned)
- Pre-commitment / Ulysses contract patterns (rate-decrease opt-ins, savings locks)
- Endowed-progress and goal-gradient effects (loan-payoff visualization)
- Social identity and reciprocity (community / cohort identity)

> **Editorial guardrail:** If you can't explain a behavioral mechanic in plain language to a member who'll experience it, the article shouldn't ship. We don't deploy mechanics we'd be uncomfortable with a journalist or regulator seeing in detail.

---

## CTAs to use in blog posts (audience-building only)

The blog's KPI right now is **demand signal**, not application volume. CTAs should map to one of these actions:

| CTA type | Sample copy | What it proves |
| --- | --- | --- |
| **Waitlist** | *"We're building this. Get on the IvyEdge waitlist — be first when we launch."* | Aggregate intent (highest-value signal) |
| **Newsletter** | *"More like this every Thursday. Get the next post in your inbox."* | Topic engagement, recurring audience |
| **Survey** | *"2-minute question: which of these has happened to you? [link]"* | Qualitative validation + segmentation |
| **Story collection** | *"Has this happened to you? Tell us your story — we read every one."* | Compounding content + persona research |
| **Share** | *"Know a freelancer who needs to read this? Send it to her."* | Organic distribution, viral coefficient |

### Sample CTA patterns for posts

> "We're building IvyEdge for exactly this — finance that sees your full story instead of a three-digit number. **Get on the waitlist** to be the first to know when we launch."

> "If your bank just told you 'no,' we want to hear about it. **[Tell us your story →]** We're building something for women who keep getting that answer, and your experience shapes what we make."

> "More truth about how finance actually works — every Thursday, from us, in plain language. **[Get the next post in your inbox →]**"

### Phrases the agent should NEVER use right now

- "Apply for an Ivy Smart Loan"
- "Check your rate"
- "See if you qualify"
- "Get pre-approved"
- "Sign up for Ivy [anything]"
- "Open your Ivy Checking account"
- "Use Ivy Credit Monitor to track your score"

If a draft contains any of those, the editor flags it back to the agent before publication.

---

## What to do if a topic naturally pulls toward a product

Some topics (e.g., "what to look for in a lender that evaluates 1099 income") will naturally pull the agent toward describing a product. When that happens:

1. Describe the **principles** — what an ideal underwriting model would weight, what a fair lender's terms would look like.
2. Tell the reader what to **ask** lenders (criteria, questions to push on, red flags).
3. Close with a waitlist or newsletter CTA: *"We're building toward exactly these principles. Get on the waitlist."*

This positions IvyEdge as the authority on the topic and creates demand for the eventual product, without claiming a product that doesn't exist.

---

## When this file changes

When IvyEdge launches its first product, replace this file with a "live products" version that brings back product CTAs. The agent prompts will also need a small edit (search for `PRE-LAUNCH CONTEXT` blocks in `ivyedge_content_agent.py`).
