import argparse
import json
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).parent
load_dotenv(PROJECT_DIR / ".env")

from graph import build  # noqa: E402
from state import init_state  # noqa: E402


def load_config() -> dict:
    return yaml.safe_load((PROJECT_DIR / "config.yaml").read_text())


def slugify(domain: str) -> str:
    return (
        domain.replace("https://", "")
        .replace("http://", "")
        .rstrip("/")
        .replace("/", "_")
    )


def _run_config(domain: str, mode: str) -> dict:
    """Build a LangGraph RunnableConfig with LangSmith-friendly run name + tags."""
    return {
        "run_name": f"agent:{slugify(domain)}",
        "tags": ["account-research-agent", mode],
        "metadata": {"domain": domain, "mode": mode},
    }


def run_agent(domain: str, app_config: dict, mode: str = "single") -> dict:
    """Run the agent and return the full final state. No file I/O."""
    graph = build(app_config)
    return graph.invoke(init_state(domain), config=_run_config(domain, mode))


def _summary(final: dict) -> dict:
    """Compact, comparison-friendly view of the final state."""
    signals = final.get("signals")
    icp = (getattr(signals, "icp_fit", None) or {}) if signals else {}
    maturity = (getattr(signals, "gtm_maturity", None) or {}) if signals else {}
    critique = final.get("critique")
    return {
        "domain": final.get("domain"),
        "icp_fit": {
            "score": icp.get("fit_score"),
            "stage_guess": icp.get("stage_guess"),
            "sector_guess": icp.get("sector_guess"),
            "confidence": icp.get("confidence"),
            "disqualifiers_hit": icp.get("disqualifiers_hit", []),
            "override_reasoning": icp.get("override_reasoning", ""),
            "evidence": icp.get("evidence", []),
        },
        "gtm_maturity": {
            "funding_stage": maturity.get("funding_stage"),
            "revops_maturity": maturity.get("revops_maturity"),
            "ai_posture": maturity.get("ai_posture"),
            "icp_clarity": maturity.get("icp_clarity"),
            "pitch_hooks": maturity.get("pitch_hooks", []),
            "disqualifiers": maturity.get("disqualifiers", []),
            "confidence": maturity.get("confidence"),
        },
        "critique": (
            {
                "final_score": critique.score,
                "iterations_used": critique.iteration,
                "unresolved_gaps": [g.dimension for g in critique.gaps],
            }
            if critique
            else None
        ),
    }


def _write_outputs(domain: str, final: dict) -> Path:
    out_dir = PROJECT_DIR / "outputs"
    out_dir.mkdir(exist_ok=True)
    slug = slugify(domain)
    brief_path = out_dir / f"{slug}.md"
    summary_path = out_dir / f"{slug}.summary.json"
    brief_path.write_text(final.get("brief") or "(no brief)")
    summary_path.write_text(json.dumps(_summary(final), indent=2, default=str))
    print(f"[done] wrote {brief_path} + {summary_path.name}")
    return brief_path


def run_one(domain: str, app_config: dict, mode: str = "single") -> dict:
    final = run_agent(domain, app_config, mode=mode)
    _write_outputs(domain, final)
    return final


def _is_complete(domain: str) -> bool:
    """A domain run is complete iff its summary.json exists and the critic
    actually produced a fit_score. Half-written outputs (process killed
    mid-write, JSON parse errors, no score) count as incomplete and re-run."""
    summary_path = PROJECT_DIR / "outputs" / f"{slugify(domain)}.summary.json"
    if not summary_path.exists():
        return False
    try:
        data = json.loads(summary_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return data.get("icp_fit", {}).get("score") is not None


def run_batch(
    path: Path, app_config: dict, force: bool = False
) -> list[tuple[str, str]]:
    """Run the agent across a list of domains. Default: skip domains that
    already have a complete output file (resume after a crash mid-batch).
    Pass force=True to re-run everything regardless."""
    domains = [
        ln.strip()
        for ln in path.read_text().splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    results: list[tuple[str, str]] = []
    for i, domain in enumerate(domains, start=1):
        print(f"\n=== [{i}/{len(domains)}] {domain}")
        if not force and _is_complete(domain):
            print(
                f"[batch] {domain}: already complete — skipping "
                f"(pass --force to re-run)"
            )
            results.append((domain, "skipped"))
            continue
        try:
            run_one(domain, app_config, mode="batch")
            results.append((domain, "ok"))
        except Exception as e:
            print(f"[batch] {domain} failed: {e}")
            results.append((domain, "err"))

    print("\n=== Batch summary")
    markers = {"ok": "OK  ", "err": "ERR ", "skipped": "SKIP"}
    for d, st in results:
        print(f"  {markers[st]} {d}")
    n_ok = sum(1 for _, st in results if st == "ok")
    n_skip = sum(1 for _, st in results if st == "skipped")
    n_err = sum(1 for _, st in results if st == "err")
    print(f"\n{n_ok} ran, {n_skip} resumed (skipped), {n_err} failed (of {len(results)} total)")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the account research agent on a domain or batch of domains."
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("domain", nargs="?", help="Single domain, e.g. stripe.com")
    grp.add_argument(
        "--batch", type=Path, help="Path to newline-separated file of domains"
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="In batch mode, re-run domains that already have complete outputs (default: skip them)",
    )
    args = parser.parse_args()

    app_config = load_config()
    if args.batch:
        results = run_batch(args.batch, app_config, force=args.force)
        return 0 if not any(st == "err" for _, st in results) else 1
    run_one(args.domain, app_config)
    return 0


if __name__ == "__main__":
    sys.exit(main())
