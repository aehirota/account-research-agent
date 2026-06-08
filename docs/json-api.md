# JSON API contract

Stable machine-readable output for downstream consumers (e.g. `meeting-prep-agent`).

## Invocation

```bash
python run.py <domain> --json
```

- Stdout: the JSON payload below (and **only** the JSON payload).
- Stderr: all diagnostic prints (orchestrator iteration logs, node prints, `[done] wrote ...` lines).
- Exit code: `0` on success, non-zero on uncaught error.
- File side effects: still writes `outputs/<domain>.md` and `outputs/<domain>.summary.json` so the audit trail is preserved regardless of invocation mode.

`--json` is single-domain mode. Combining it with `--batch` is rejected by argparse.

## Schema (v1.0)

```jsonc
{
  "schema_version": "1.0",         // bump on breaking changes
  "domain": "vercel.com",
  "summary": {
    "domain": "vercel.com",
    "icp_fit": {
      "score": 4.0,                // 0.0–5.0; clamped ≤1.0 when disqualifier hits without override
      "stage_guess": "series-f",
      "sector_guess": "developer platform / AI cloud infrastructure",
      "confidence": 0.85,
      "disqualifiers_hit": ["series-d-plus-legacy"],
      "override_reasoning": "COO Grosser published Nov-2025 thesis on hiring GTM Engineers; Vercel Agent internal deployment confirms agentic GTM build. Concrete dated signals of active GTM infra investment.",
      "evidence": [
        {"claim": "...", "source": "https://..."}
      ]
    },
    "gtm_maturity": {
      "funding_stage": "series-f",
      "revops_maturity": 4,        // 1–5
      "ai_posture": "ai-native",
      "icp_clarity": "high",
      "pitch_hooks": ["...", "..."],
      "disqualifiers": [],
      "confidence": 0.80
    },
    "critique": {
      "final_score": 4.5,          // 0.0–5.0 critic score
      "iterations_used": 2,        // capped at config.graph.max_iterations
      "unresolved_gaps": []        // dimension names of any gaps the agent ran out of iterations on
    }
  },
  "brief_markdown": "# Vercel — ICP-fit account brief\n\n..."   // full markdown brief
}
```

## Schema stability

Bump `schema_version` on:

- Removing or renaming a field
- Changing a field's type (e.g. `int` → `string`)
- Changing the semantics of a value (e.g. score range)

Adding new optional fields does not require a bump — consumers should ignore unknown fields.

## Consumer guidance

Downstream consumers (e.g. `meeting-prep-agent`) should:

1. Pin to a tagged release of this repo (e.g. `v1.1.0`) rather than `main` — schema changes between releases are version-controlled.
2. Read `schema_version` and refuse to proceed if it exceeds the version they were written against.
3. Treat `brief_markdown` as opaque human-readable text. If you need structured reasoning, use the `summary` fields.
4. Handle `summary.icp_fit.evidence` and `summary.gtm_maturity.pitch_hooks` as possibly-empty lists.

## Example

```bash
# from another repo:
out=$(python /path/to/account-research-agent/run.py vercel.com --json 2>/tmp/ara.log)
echo "$out" | jq '.summary.icp_fit.score'
# -> 4.0
```
