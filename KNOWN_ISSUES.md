# Known Issues

Honest disclosure of what's currently wrong, in-progress, or under audit.

## Eval golden curation — vendor-vs-buyer mistake

**Status:** ~11 of 20 goldens currently under audit. Discovered 2026-06-05 during sample brief review.

**What happened:** Initial golden curation picked companies with strong AI/GTM signals (Clay, Apollo, Gong, Outreach, 11x.ai, Pocus, Regie, Mutiny, Vitally, HockeyStack, Default) as "strong fit" or "good fit." On running the agent against them, every single one came back as **Pass (Pursue: n/a)** — because they are all **vendors of GTM tooling** (the category Anderson uses), not **buyers of GTM Engineering services**.

The agent is correct. The goldens were wrong.

A real buyer for Anderson's services is a Series A–C B2B SaaS in a **non-GTM-adjacent sector** (devtools, fintech, healthtech, vertical SaaS) that's hiring GTM Engineers internally. The original goldens conflated "matches the surface signals" with "is actually a buyer." The agent's job is exactly to penetrate that surface distinction — and it does, which is the whole portfolio point.

See [outputs/samples/peer-not-buyer-clay.md](outputs/samples/peer-not-buyer-clay.md) and [outputs/samples/peer-not-buyer-apollo.md](outputs/samples/peer-not-buyer-apollo.md) for the agent's reasoning on this distinction.

**Affected goldens — vendor-disguised-as-buyer:**
- `clay.com`, `apollo.io`, `11x.ai`, `pocus.com`, `default.com`, `regie.ai` (labeled strong fit, scored 0–1 by agent)
- `gong.io`, `outreach.io`, `mutinyhq.com`, `vitally.io`, `hockeystack.com` (labeled good fit, expected to score similarly)

**Affected goldens — late-stage-mislabeled-as-mid-fit:**

Discovered 2026-06-05 during batch 2 review. The same surface-vs-reality mistake but a different root cause: I picked "mid fit" / "weak fit" candidates from well-known SaaS companies that are actually now post-Series-C. The agent correctly fires the `series-d-plus-legacy` disqualifier on all of them.

- `posthog.com` — agent identified Series E (raised 2024)
- `figma.com` — agent identified IPO 2025
- `stripe.com` — already fixed in golden_accounts.jsonl on 2026-06-05

**The deeper observation:** Most well-known B2B SaaS is now post-Series C. Building a "mid fit" golden class from companies I can name off the top of my head doesn't work — those companies are all already too big. Real Series A–C non-GTM-adjacent buyers are obscure by definition. Sourcing them requires deliberate research, not surface intuition.

**One validated buyer-class pass:** `retool.com` was the only golden across both batches that came back as a clean strong-fit pass (4.0/5, pursue, no disqualifier) with rich pitch angle. See [outputs/samples/buyer-fit-retool.md](outputs/samples/buyer-fit-retool.md). This validates the architecture works on genuine buyer-class accounts when they exist in the golden set.

**Resolution plan:**
1. Eval is being re-scoped: disqualifier-class goldens are the canonical signal (deloitte, mckinsey, gymshark, vercel-override, stripe — all PASS). Vendor-class and stage-mislabeled accounts kept only as edge-case demonstrations.
2. Buyer-class candidates to add when curated: obscure Series A–C devtools, fintech, healthtech, vertical SaaS with public GTM Engineer/RevOps hires. Famous SaaS is mostly disqualified.
3. Until then, eval validity claims should be made against the **disqualifier-class + retool.com only** (6 PASS goldens).

**Resolution progress (2026-06-11):**
- `clay.com` + `apollo.io` goldens re-curated to disqualifier-class per this adjudication (expected `disqualified: true`, fit 1±1) — they're now canonical vendor/buyer-confusion drift catchers in the [eval-watch](https://github.com/aehirota/eval-watch) subset rather than known-wrong expectations.
- ICP config gained an explicit `peer-gtm-vendor` sector disqualifier, and the extractor prompt now names the two mislabeling traps directly: peer vendor ≠ buyer (surface signals don't override), and stage = funding-round letter, not valuation (a Series C at $3B is still Series C — previously the agent reached the right verdict on clay.com via the wrong rule, flagging `series-d-plus-legacy` on a Series C company).
- Remaining mislabeled goldens (11x.ai, pocus, default, regie, gong, outreach, mutiny, vitally, hockeystack, posthog, figma) still pending re-curation — item 2 above unchanged.

## Quality variance on `gtm_maturity.revops_maturity` scoring

**Status:** Known limitation, documented in [evals/rubric.md](evals/rubric.md). The check is optional, not mandatory.

The `gtm_maturity` node uses Haiku 4.5 (for Tier 1 Anthropic rate-limit reasons) and is fragile on subtle "is this company's RevOps mature?" judgments. Same company can score 3/5 on one iteration and 0/5 on the next given the same raw data. Mature companies (Stripe, Vercel) sometimes score 0/5 despite having visible RevOps orgs.

Won't fix until moving to Tier 2+ Anthropic, at which point `models.extractor` flips from Haiku to Sonnet (one config line in `config.yaml`).

## `jobs_reader` returns 0 roles on third-party ATS sites

**Status:** Known limitation, documented in [nodes/jobs_reader.py](nodes/jobs_reader.py).

Companies hosting careers on Greenhouse/Lever/Ashby/Workday only link out from their `/careers` page. Firecrawl returns the link-out page successfully but with no job content, so `jobs_reader` extracts 0 roles. The `web_enricher` typically compensates by searching for "{company} jobs" and surfacing role intel from news/social/job-board mentions.

Future fix: detect ATS link patterns in the scraped page and follow them in a second scrape pass.
