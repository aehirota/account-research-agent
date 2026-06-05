"""Run the agent against the golden accounts and report pass/fail per check.

Usage:
    python evals/eval_runner.py                # run all goldens
    python evals/eval_runner.py --domain clay.com   # run a single golden by domain
    python evals/eval_runner.py --dry-run      # show goldens and skip execution

Exits non-zero if any mandatory check fails on any account.
"""
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(PROJECT_DIR / ".env")

from run import load_config, run_agent, slugify  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_accounts.jsonl"


@dataclass
class CheckResult:
    name: str
    mandatory: bool
    passed: bool
    detail: str


def load_goldens() -> list[dict]:
    with open(GOLDEN_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


def _icp(final) -> dict:
    sig = final.get("signals")
    return (getattr(sig, "icp_fit", None) or {}) if sig else {}


def _maturity(final) -> dict:
    sig = final.get("signals")
    return (getattr(sig, "gtm_maturity", None) or {}) if sig else {}


def _critique(final):
    return final.get("critique")


def score_account(golden: dict, final: dict, config: dict) -> list[CheckResult]:
    expected = golden["expected"]
    icp = _icp(final)
    maturity = _maturity(final)
    critique = _critique(final)

    results: list[CheckResult] = []

    # 1. Disqualifier identification (mandatory)
    # Under the hybrid disqualifier policy, we check whether the agent
    # *identified* a disqualifier — independent of whether it chose to override.
    # An override is a transparent product decision, not a missed signal.
    actual_disq = bool(icp.get("disqualifiers_hit"))
    expected_disq = expected.get("disqualified", False)
    override = (icp.get("override_reasoning") or "").strip()
    detail = (
        f"actual={actual_disq} expected={expected_disq} "
        f"hits={icp.get('disqualifiers_hit')} "
        + (f"override={'yes' if override else 'no'}" if actual_disq else "")
    )
    results.append(
        CheckResult(
            "disqualifier_identified",
            mandatory=True,
            passed=actual_disq == expected_disq,
            detail=detail,
        )
    )

    # 2. Fit score within tolerance (mandatory, but SKIPPED for disqualifier
    # goldens — under hybrid policy the agent may legitimately score them
    # higher via an override, so fit_score is not a useful regression signal.)
    actual_fit = icp.get("fit_score")
    expected_fit = expected["fit_score"]
    tol = expected.get("fit_tolerance", 1)
    if expected_disq:
        results.append(
            CheckResult(
                "fit_score_within_tolerance",
                mandatory=False,  # informational only for disqualifier goldens
                passed=True,
                detail=f"skipped (disqualifier golden) actual={actual_fit} expected={expected_fit}±{tol}",
            )
        )
    else:
        fit_ok = actual_fit is not None and abs(actual_fit - expected_fit) <= tol
        results.append(
            CheckResult(
                "fit_score_within_tolerance",
                mandatory=True,
                passed=fit_ok,
                detail=f"actual={actual_fit} expected={expected_fit}±{tol}",
            )
        )

    # 3. Min confidence (mandatory)
    actual_conf = icp.get("confidence")
    min_conf = expected.get("min_confidence", 0.0)
    results.append(
        CheckResult(
            "min_confidence",
            mandatory=True,
            passed=actual_conf is not None and actual_conf >= min_conf,
            detail=f"actual={actual_conf} >= {min_conf}",
        )
    )

    # 4. AI posture in allowed set (mandatory)
    actual_posture = maturity.get("ai_posture")
    allowed = set(expected.get("ai_posture_in", []))
    results.append(
        CheckResult(
            "ai_posture_in_allowed",
            mandatory=True,
            passed=actual_posture in allowed,
            detail=f"actual={actual_posture} allowed={sorted(allowed)}",
        )
    )

    # 5. RevOps maturity floor (optional)
    actual_revops = maturity.get("revops_maturity")
    min_revops = expected.get("min_revops_maturity", 0.0)
    results.append(
        CheckResult(
            "revops_maturity_floor",
            mandatory=False,
            passed=actual_revops is not None and actual_revops >= min_revops,
            detail=f"actual={actual_revops} >= {min_revops}",
        )
    )

    # 6. Critic converged (optional)
    threshold = config["graph"]["critic_pass_threshold"]
    max_iter = config["graph"]["max_iterations"]
    if critique is not None:
        score = critique.score
        iters = critique.iteration
        converged = (score is not None and score >= threshold) or iters >= max_iter
    else:
        converged = False
        score = None
        iters = None
    results.append(
        CheckResult(
            "critic_converged",
            mandatory=False,
            passed=converged,
            detail=f"score={score} iters={iters} (threshold={threshold}, max_iter={max_iter})",
        )
    )

    return results


def dump_state(final: dict, domain: str) -> Path:
    out_dir = PROJECT_DIR / "outputs"
    out_dir.mkdir(exist_ok=True)
    state_path = out_dir / f"eval_{slugify(domain)}.state.json"
    brief_path = out_dir / f"eval_{slugify(domain)}.md"

    serializable = {
        "domain": final.get("domain"),
        "brief": final.get("brief"),
        "signals": {
            "icp_fit": _icp(final),
            "gtm_maturity": _maturity(final),
        },
        "critique": (
            {
                "score": _critique(final).score,
                "iteration": _critique(final).iteration,
                "gaps": [g.model_dump() for g in _critique(final).gaps],
                "history": _critique(final).history,
            }
            if _critique(final)
            else None
        ),
    }
    state_path.write_text(json.dumps(serializable, indent=2, default=str))
    brief_path.write_text(final.get("brief") or "(no brief)")
    return state_path


def print_account_report(golden: dict, results: list[CheckResult]) -> bool:
    """Print per-check results. Return True if all mandatory checks passed."""
    domain = golden["domain"]
    label = golden.get("label", "")
    print(f"\n=== {domain} — {label}")
    all_mand_pass = True
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        mand = "[mand]" if r.mandatory else "[opt] "
        print(f"  {marker} {mand} {r.name:30s} {r.detail}")
        if r.mandatory and not r.passed:
            all_mand_pass = False
    return all_mand_pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", help="Run only the golden matching this domain")
    parser.add_argument("--dry-run", action="store_true", help="List goldens and exit")
    args = parser.parse_args()

    goldens = load_goldens()
    if args.domain:
        goldens = [g for g in goldens if g["domain"] == args.domain]
        if not goldens:
            print(f"No golden found for domain={args.domain}", file=sys.stderr)
            return 2

    if args.dry_run:
        print(f"{len(goldens)} golden account(s):")
        for g in goldens:
            print(f"  - {g['domain']}: {g.get('label', '')}")
        return 0

    config = load_config()
    summary: list[tuple[str, bool]] = []

    for golden in goldens:
        domain = golden["domain"]
        try:
            final = run_agent(domain, config, mode="eval")
        except Exception as e:
            print(f"\n=== {domain} — FATAL: {e}")
            summary.append((domain, False))
            continue

        results = score_account(golden, final, config)
        passed = print_account_report(golden, results)
        dump_state(final, domain)
        summary.append((domain, passed))

    print("\n=== Summary")
    n_pass = sum(1 for _, p in summary if p)
    for d, p in summary:
        print(f"  {'PASS' if p else 'FAIL'}  {d}")
    print(f"\n{n_pass}/{len(summary)} accounts passed all mandatory checks")

    return 0 if n_pass == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
