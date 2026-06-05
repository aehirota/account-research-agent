# Account Research Agent

LangGraph agent that takes a company domain and produces a structured account brief scored against Anderson's ICP (Series A–C B2B SaaS hiring GTM Engineers / running RevOps modernization).

Built as a learning vehicle for architect-level LangGraph patterns (state machine + critic-and-retry loop) and as a real Phase A prospecting tool.

## Status

**Day 1 ✓** — linear scaffold, Anthropic SDK + Firecrawl plumbing validated.

**Day 2 ✓** — full architecture wired:
- Parallel research: `scraper` (with inline jobs extraction) + `web_enricher` fan out from orchestrator
- Extractors: `icp_extractor` + `gtm_maturity` join after research, run in parallel
- `critic` scores 0–5 and emits structured `Gap` objects with `target_node`
- Conditional loop: orchestrator reads gaps, re-dispatches only the nodes that own each gap (e.g. funding gap → re-fire `web_enricher` only)
- Hard cap at 3 iterations; emits brief with "known gaps" footer if it bails out
- `web_enricher` uses Anthropic native `web_search` tool (no Tavily key needed)

**V1 ✓** — production polish + portfolio harness (see same-named section below).

**V1.1 ✓ (current)** — hybrid disqualifier policy:
- `icp_extractor` may flag a disqualifier (`series-d-plus-legacy`, `consultancy`, etc.) AND optionally override it with `override_reasoning` citing a specific signal
- Code clamps `fit_score ≤ 1` automatically when a disqualifier hits and override is empty (LLM can't accidentally "forget" the rule)
- Brief surfaces overrides in a dedicated "Disqualifier override" section so the reader can audit the agent's judgment
- Eval check renamed to `disqualifier_identified` — measures whether the agent *saw* the disqualifier, not whether it scored it 1. Overrides are a transparent product call, not a regression.

**Day 3 ✓** — eval harness:
- Golden accounts in `evals/golden_accounts.jsonl` spanning the ICP fit spectrum
- 4 mandatory + 2 optional checks per account (see `evals/rubric.md`)
- `python evals/eval_runner.py` runs all goldens, prints per-check results, exits non-zero on any mandatory failure
- Per-account state dumped to `outputs/eval_<domain>.state.json` for debugging

**V1 ✓** — production polish + portfolio harness:
- **LangSmith tracing** wired (set `LANGSMITH_TRACING=true` + `LANGSMITH_API_KEY` in `.env` to enable). Each run is tagged with mode (`single`/`batch`/`eval`) and domain for trace discoverability.
- **Batch mode**: `python run.py --batch evals/domains.sample.txt` runs sequentially, writes brief + compact `<domain>.summary.json` per account, prints aggregate summary.
- **Golden set expanded to 20** accounts: 6 strong-fit, 5 good-fit, 5 mid/weak, 3 disqualified, 1 enterprise edge case.
- **Clay comparison harness**: `python evals/clay_comparison.py --clay clay_export.csv` produces a side-by-side markdown scorecard (direction agreement %, signals the agent surfaced beyond Clay's columns). Requires you to build the Clay table manually first.

## Setup

```bash
cd projects/account-research-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill in ANTHROPIC_API_KEY and FIRECRAWL_API_KEY
```

Firecrawl free tier gives ~500 scrapes/month. Each domain currently hits ~6 pages, so budget ~80 domains/month on free.

## Usage

Single run:

```bash
python run.py vercel.com
# -> outputs/vercel.com.md + outputs/vercel.com.summary.json
```

Batch run (resumable by default — skips domains whose outputs already exist):

```bash
python run.py --batch evals/domains.sample.txt
# -> outputs/<domain>.md + outputs/<domain>.summary.json for each
# A domain is "complete" when its summary.json has a non-null fit_score.
# Half-written outputs from a crashed run are re-run automatically.

python run.py --batch evals/domains.sample.txt --force
# Re-run everything regardless of existing outputs.
```

Eval against goldens:

```bash
python evals/eval_runner.py                       # all 20 goldens
python evals/eval_runner.py --domain clay.com     # one golden
python evals/eval_runner.py --dry-run             # list without executing
```

Clay vs. agent comparison (requires a Clay export CSV with at minimum `domain`, `clay_fit_score`, `clay_disqualified` columns):

```bash
# 1. Build a Clay table for the same ICP. Export to clay_export.csv.
# 2. Run the agent on the same domains.
python run.py --batch evals/domains.sample.txt
# 3. Render the scorecard.
python evals/clay_comparison.py --clay clay_export.csv
# -> outputs/comparison.md
```

Output is a one-screen markdown brief: ICP fit score, evidence, recommendation, pitch angle. Eval also dumps `outputs/eval_<domain>.state.json` for inspection.

Cost: each run ≈ **$0.25–$0.40** per account. Full 20-golden eval ≈ **$5–$8** and ~15 minutes wall-clock. See `evals/rubric.md` for the breakdown and cost-reduction levers.

## LangSmith tracing

When `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` are set, LangGraph automatically traces every run. Each run gets a `run_name` like `agent:vercel.com` and tags `account-research-agent` + mode (`single` / `batch` / `eval`). Use the LangSmith UI to inspect critic loop iterations, per-node latency, and token costs.

## Architecture

```
                START
                  v
            orchestrator  <----------------------+
                  |                              |
       [conditional Send: cold start /           |
        gap-targeted re-fire / done]             |
                /        \                       |
               v          v                      |
           scraper     web_enricher              |
           (+inline                              |
            jobs)                                |
                \        /                       |
                 v      v                        |
           icp_extractor   gtm_maturity          |
                    \         /                  |
                     v       v                   |
                       critic                    |
                         |                       |
                  [score >= threshold            |
                   OR iter >= max] -> compiler -> END
                         |
                         +------(else)-----------+
```

`scraper` and the (formerly separate) `jobs_reader` are collapsed into one node because LangGraph's default channels fire whenever a predecessor produces — not when all predecessors complete. Sequential branches that converge at different super-steps caused extractors to fire twice per iteration. Combining them into one node makes both research branches finish in the same super-step, so extractors fire exactly once. Trade-off: a critic gap targeting "jobs re-extraction only" must re-fire the whole scrape (cheap on Firecrawl free tier).

The critic emits structured `Gap` objects with `target_node ∈ {scraper, web_enricher, icp_extractor, gtm_maturity}`. The orchestrator's conditional edge reads gaps with severity ≥ `mandatory_gap_severity` (default 0.7) and re-dispatches **only** the nodes that own them:
- "funding stage unknown" → re-fire `web_enricher` with a hint
- "pricing not scraped" → re-fire `scraper` with hint `/pricing`
- "ICP extraction misweighted the GTM job posting" → re-fire `icp_extractor` only

Multiple gap-owning nodes re-fire in parallel via LangGraph's `Send` API. Extractors re-run automatically after any research re-fire, and the critic re-evaluates. Hard cap at `graph.max_iterations` (default 3).

## Disqualifier policy (hybrid)

The agent does NOT auto-fail a company because a disqualifier hit. It has two paths:

| Path | When | Result |
|---|---|---|
| **A — Honor (default)** | `disqualifiers_hit` non-empty AND `override_reasoning` empty | Code clamps `fit_score` to ≤ 1. Brief recommends pass. |
| **B — Override** | `disqualifiers_hit` non-empty AND `override_reasoning` populated with a cited signal | `fit_score` may be 3–5. Brief shows a dedicated "Disqualifier override" section so the reader can audit the call. |

Code enforcement lives in [nodes/icp_extractor.py](nodes/icp_extractor.py) — the LLM can't accidentally skip the rule. The override field must be a single sentence with a concrete, dated, source-attributable signal (e.g. "COO Grosser published a Nov-2025 thesis on GTM Engineer hiring"). Stage disqualifiers (Series D+, pre-seed) can be overridden by clear buyer-side intent; sector disqualifiers (consultancy, agency, marketplace) should almost never be overridden — see [prompts/icp_extractor.md](prompts/icp_extractor.md) for the full policy.

The eval check measures whether the agent *identified* the disqualifier, not whether it scored it 1. A missed disqualifier is a regression; an overridden one is a transparent product judgment.

## Config & tuning knobs

`config.yaml` holds the ICP definition, model choices, max iterations, scrape page list. Edit this to retarget the agent. Prompts in `prompts/*.md` interpolate the ICP YAML so behavior follows config.

Key knobs:

| Knob | Default | When to change |
|---|---|---|
| `graph.max_iterations` | 3 | Raise to 4-5 if briefs consistently bail at max-iter with `unresolved_gaps`; lower to 2 to halve cost on confident accounts. |
| `graph.critic_pass_threshold` | 4.0 | Raise to 4.5 for stricter briefs; lower to 3.5 to cut iteration cost. |
| `graph.mandatory_gap_severity` | 0.7 | Lower to 0.5 to make the critic loop more aggressive about re-firing nodes; raise to 0.9 to only act on critical gaps. |
| `web_enricher.max_uses` | 2 | Each web_search adds ~1.5k cumulative tokens. Tier 1 Anthropic (30k Sonnet tokens/min) caps at ~3. Raise to 4-5 on Tier 2+ for deeper searches. |
| `models.extractor` | Haiku 4.5 | Stays on Haiku to avoid Tier 1 Sonnet rate limits when firing in parallel with `web_enricher`. Switch to Sonnet on Tier 2+ for stronger reasoning. |
| `scraper.pages_to_attempt` | `/`, `/about`, `/careers`, `/jobs`, `/customers`, `/pricing` | Add ICP-specific paths (e.g. `/integrations`, `/security`) if your goldens depend on them. |

## Known limitations

- **`jobs_reader` misses third-party ATS.** Many companies host their careers page on Greenhouse, Lever, Ashby, or Workday and only link out from `/careers`. Firecrawl returns the link-out page but it has no job content, so the structured jobs extract returns 0 roles. The `web_enricher` typically compensates by searching for "{company} jobs" or "{company} hiring", and the critic flags the missing data correctly. Future fix: detect ATS link patterns in the scraped page and follow them in a second scrape pass.
- **LLM scoring variance across runs.** Same input can yield different `fit_score` and `gtm_maturity.revops_maturity` values across runs (±0.5 typical, ±1.5 worst case). Use the eval as a directional regression catcher, not a calibration tool.
- **Anthropic Tier 1 rate limits drive model choice.** Extractors run on Haiku because parallel Sonnet calls burst past the 30k tokens/min limit. Sonnet is reserved for `web_enricher` (needs tool-use loop), `critic`, and `compiler`. Move all to Sonnet once on Tier 2+ if reasoning quality matters more than cost.

## Stack

- LangGraph (orchestration) — `Send` API for conditional parallel fanout, `Annotated` state with reducers (`merge_raw`, `merge_signals`) so parallel writes to the same key merge instead of clobber
- Anthropic SDK direct (no LangChain wrappers)
  - **Sonnet 4.6** — `web_enricher` (tool-use loop), `critic`, `compiler`
  - **Haiku 4.5** — `icp_extractor`, `gtm_maturity` (Tier 1 rate-limit reasons), inline jobs extraction inside `scraper`, web findings structuring
- Pydantic for state + structured node outputs via forced tool use; `_heal_input()` in [tools/anthropic_client.py](tools/anthropic_client.py) repairs Haiku's occasional JSON-stringified list quirk before validation
- Firecrawl (managed) for scraping; soft-fails per-page so a single 404 doesn't crash the run
- WebSearch via Anthropic native `web_search` server-side tool — no Tavily/Brave key needed

## File layout

```
state.py             Pydantic state schema (AccountResearchState, Gap, Critique, ...)
graph.py             LangGraph wiring
run.py               CLI entrypoint
config.yaml          ICP + model + graph config
prompts/             system prompts as .md files (version-controlled)
nodes/               one file per LangGraph node
tools/               shared clients (Anthropic SDK wrapper, Firecrawl)
evals/               golden accounts + eval runner (Day 3)
outputs/             generated briefs (gitignored)
```

## Why no SQLite / no Apollo / no Clay

This project intentionally avoids the patterns from `icp-research-agent-amfinn-v2/`. Different problem:
- That project: bulk prospect generation (Apollo search → score N companies → CSV)
- This project: single-account deep research with self-correction (one domain → detailed brief via critic loop)

Bulk generation is a downstream consumer of this. Could be wired in later — point a list runner at this agent.

## Portfolio angle

Side-by-side comparison piece: run the same N companies through (a) a Clay table for the same ICP, (b) this agent. Use `python evals/clay_comparison.py --clay clay_export.csv` to render the scorecard. Score on completeness, signal quality, edge-case handling, cost/account, time-to-add-new-ICP-criterion. Output: case study under `kb/case-studies.md` + LinkedIn post + writeup on gtmsystemslab.com.

The Vercel run is particularly strong portfolio material — the hybrid disqualifier policy surfaces a clear "Disqualifier override" section explaining why a Series F company would still warrant outreach (live GTM Engineer JD + COO Grosser's published thesis). That's the architect-vs-operator demonstration Clay can't produce.
