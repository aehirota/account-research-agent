# Eval Rubric

The eval runner runs each golden account through the full agent and scores the final state on the dimensions below. A golden account passes when **all** mandatory checks pass and **at least** the required number of optional checks pass.

Treat eval signal as directional, not absolute — prompts will drift, the web changes, and a 4 vs. a 3 on a borderline company is not a regression. Look for systematic drops across multiple accounts.

## Mandatory checks (must pass)

### 1. Disqualifier identification
`bool(actual.disqualifiers_hit) == expected.disqualified`

Under the **hybrid disqualifier policy** the agent is allowed to *override* a disqualifier by populating `override_reasoning` with a specific signal. The eval does NOT punish overrides — it only checks whether the agent *identified* the disqualifying signal in the first place. An override is a transparent product judgment; a missed disqualifier is a regression.

If a golden is labeled as a disqualifier (e.g. agency, consultancy, late-stage SaaS), the agent must populate `disqualifiers_hit`. If not, must hit none.

### 2. ICP fit score within tolerance (non-disqualifier goldens only)
`abs(actual.fit_score - expected.fit_score) <= expected.fit_tolerance`

Default tolerance is 1. Catches systematic scoring drift.

**SKIPPED on disqualifier goldens** — under the hybrid policy, the agent may legitimately score a disqualified account 4–5 via override (e.g. Vercel is Series F but the COO is publicly building the GTM Engineer role). `fit_score` is therefore not a useful regression signal for these accounts; the eval marks the check informational rather than mandatory.

### 3. Minimum confidence
`actual.icp_fit.confidence >= expected.min_confidence`

Low confidence on a well-known company means the research didn't gather enough — failure mode worth catching.

### 4. AI posture in allowed set
`actual.gtm_maturity.ai_posture in expected.ai_posture_in`

Most goldens allow a range; the disqualifier-style accounts allow anything (this check effectively becomes informational for them).

## Optional checks (informational)

### 5. RevOps maturity floor
`actual.gtm_maturity.revops_maturity >= expected.min_revops_maturity`

Soft signal — RevOps maturity is harder to score reliably than ICP fit.

### 6. Critic converged
`final.critique.score >= config.graph.critic_pass_threshold OR iteration == max_iterations`

Catches infinite-loop bugs or cases where the loop bailed out unexpectedly early.

## Reporting

`python evals/eval_runner.py` prints:
- Per-account: each check with pass/fail + actual vs. expected
- Summary: mandatory pass rate across all accounts
- Exit code: 0 if all mandatory checks pass on all accounts; 1 otherwise

For deeper debugging, briefs are written to `outputs/eval_<domain>.md` alongside a `outputs/eval_<domain>.state.json` dump of the final state for inspection.

## Cost note

**Per-account: ~$0.25–$0.40.** Full 5-account eval: ~$1–$2 and ~5 minutes wall-clock.

Breakdown (cold-start pass, Sonnet 4.6 ~$3/M input, $15/M output):

| Call | Model | Cost |
|---|---|---|
| icp_extractor | Sonnet | $0.035 |
| gtm_maturity | Sonnet | $0.032 |
| critic | Sonnet | $0.011 |
| compiler | Sonnet | $0.011 |
| web_enricher search + 5 searches @ $10/1k | Sonnet | $0.083 |
| web_enricher structure | Haiku | <$0.01 |
| jobs_reader | Haiku | <$0.01 |
| Firecrawl scrape (free tier) | — | $0 |

Cold pass: ~$0.20. With avg 1.5 selective critic re-fires: ~$0.30. Mostly stable across companies.

System prompts are cached (`cache_control: ephemeral` in `tools/anthropic_client.py`) so re-runs hit ~90% cache discount on the prompt portion.

### Cost-reduction levers if you want to push lower

1. **Move icp_extractor + gtm_maturity to Haiku 4.5 first pass.** Escalate to Sonnet only if critic flags "extractor_reasoning_weak". Cuts per-account ~$0.04 (~15% off total).
2. **Drop web_enricher max_uses from 5 → 3.** Cuts search fees from $0.05 → $0.03 per account.
3. **Truncate scraped page text more aggressively** (currently 6000 chars/page → 3000). Cuts icp_extractor input tokens ~50%, saves ~$0.02/account.
4. **Make web_enricher critic-gated, not cold-start.** Skip on iter 0 if site scrape + jobs are signal-rich; only fire if critic flags a funding/news gap. Cuts ~$0.08 on confident accounts.

None of these are MVP — the priority is correctness signal from the eval first. Tune cost after you've seen one full eval run and know which calls are actually pulling weight.
