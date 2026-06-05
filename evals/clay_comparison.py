"""Side-by-side scorecard: Clay table vs. this agent for the same domain set.

Workflow:
1. Build a Clay table with the same ICP definition (config.yaml). Export to CSV
   with at minimum columns: domain, clay_fit_score, clay_disqualified.
   Optional extra columns are passed through to the scorecard.
2. Run the agent in batch mode on the SAME domains:
       python run.py --batch domains.txt
   This produces outputs/<domain>.summary.json + outputs/<domain>.md.
3. Run this comparison:
       python evals/clay_comparison.py \\
           --clay clay_export.csv \\
           --outputs outputs \\
           --report comparison.md

The harness scores agreement on direction (both pursue / both pass / divergent),
flags evidence the agent surfaced that Clay couldn't, and exits non-zero only on
data errors (missing summaries, malformed CSV) — not on disagreement, which is
the entire point of the comparison.
"""
import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PROJECT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from run import slugify  # noqa: E402


@dataclass
class ClayRow:
    domain: str
    fit_score: Optional[float]
    disqualified: bool
    raw: dict  # all CSV columns, for the scorecard


@dataclass
class AgentRow:
    domain: str
    fit_score: Optional[float]
    disqualifiers_hit: list
    override_reasoning: str
    confidence: Optional[float]
    stage_guess: Optional[str]
    sector_guess: Optional[str]
    ai_posture: Optional[str]
    revops_maturity: Optional[float]
    evidence: list
    pitch_hooks: list
    final_score: Optional[float]
    iterations_used: Optional[int]
    unresolved_gaps: list


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    return str(v).strip().lower() in {"true", "yes", "1", "y", "t"}


def load_clay(csv_path: Path) -> dict[str, ClayRow]:
    rows: dict[str, ClayRow] = {}
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        if "domain" not in (reader.fieldnames or []):
            raise SystemExit(f"Clay CSV missing required column 'domain' in {csv_path}")
        for raw in reader:
            domain = (raw.get("domain") or "").strip()
            if not domain:
                continue
            rows[domain.lower()] = ClayRow(
                domain=domain,
                fit_score=_to_float(raw.get("clay_fit_score") or raw.get("fit_score")),
                disqualified=_to_bool(
                    raw.get("clay_disqualified") or raw.get("disqualified")
                ),
                raw=raw,
            )
    return rows


def load_agent(outputs_dir: Path, domain: str) -> Optional[AgentRow]:
    summary_path = outputs_dir / f"{slugify(domain)}.summary.json"
    if not summary_path.exists():
        return None
    data = json.loads(summary_path.read_text())
    icp = data.get("icp_fit") or {}
    mat = data.get("gtm_maturity") or {}
    crit = data.get("critique") or {}
    return AgentRow(
        domain=domain,
        fit_score=_to_float(icp.get("score")),
        disqualifiers_hit=icp.get("disqualifiers_hit") or [],
        override_reasoning=(icp.get("override_reasoning") or "").strip(),
        confidence=_to_float(icp.get("confidence")),
        stage_guess=icp.get("stage_guess"),
        sector_guess=icp.get("sector_guess"),
        ai_posture=mat.get("ai_posture"),
        revops_maturity=_to_float(mat.get("revops_maturity")),
        evidence=icp.get("evidence") or [],
        pitch_hooks=mat.get("pitch_hooks") or [],
        final_score=_to_float(crit.get("final_score")),
        iterations_used=crit.get("iterations_used"),
        unresolved_gaps=crit.get("unresolved_gaps") or [],
    )


def _direction(fit_score: Optional[float], disqualified: bool) -> str:
    """Boil a row down to one of: pursue / nurture / pass / unknown."""
    if disqualified:
        return "pass"
    if fit_score is None:
        return "unknown"
    if fit_score >= 4:
        return "pursue"
    if fit_score >= 3:
        return "nurture"
    return "pass"


def render(clay: dict[str, ClayRow], outputs_dir: Path) -> str:
    lines: list[str] = []
    lines.append("# Clay table vs. Account Research Agent\n")
    lines.append(
        "Comparison of the same ICP applied via a Clay table vs. this LangGraph agent. "
        "Each row reports the direction both methods reached (`pursue` / `nurture` / `pass`) "
        "plus the signal the agent surfaced beyond Clay's scoring columns.\n"
    )

    agree = 0
    disagree = 0
    missing = 0
    rows: list[dict] = []

    for key, c in clay.items():
        a = load_agent(outputs_dir, c.domain)
        if a is None:
            missing += 1
            rows.append({
                "domain": c.domain,
                "clay_dir": _direction(c.fit_score, c.disqualified),
                "agent_dir": "missing",
                "delta": "agent run not found",
                "agent": None,
                "clay": c,
            })
            continue
        clay_dir = _direction(c.fit_score, c.disqualified)
        agent_dir = _direction(a.fit_score, bool(a.disqualifiers_hit))
        if clay_dir == agent_dir:
            agree += 1
        else:
            disagree += 1
        rows.append({
            "domain": c.domain,
            "clay_dir": clay_dir,
            "agent_dir": agent_dir,
            "delta": "agree" if clay_dir == agent_dir else f"clay→{clay_dir} / agent→{agent_dir}",
            "agent": a,
            "clay": c,
        })

    total_compared = agree + disagree
    lines.append("## Summary\n")
    lines.append(f"- Domains in Clay CSV: **{len(clay)}**")
    lines.append(f"- Domains with agent run: **{len(clay) - missing}**")
    if total_compared:
        agree_pct = round(100 * agree / total_compared)
        lines.append(f"- Direction agreement: **{agree}/{total_compared} ({agree_pct}%)**")
    if missing:
        lines.append(f"- Missing agent runs: **{missing}** — run `python run.py --batch <domains>` first")
    lines.append("")

    lines.append("## Per-account\n")
    lines.append("| Domain | Clay fit | Agent fit | Direction | Δ |")
    lines.append("|---|---|---|---|---|")
    for r in rows:
        a = r["agent"]
        c = r["clay"]
        clay_fit = "—" if c.fit_score is None else f"{c.fit_score:g}"
        if c.disqualified:
            clay_fit += " (DQ)"
        if a is None:
            agent_fit = "missing"
        else:
            agent_fit = "—" if a.fit_score is None else f"{a.fit_score:g}"
            if a.disqualifiers_hit:
                agent_fit += " (DQ)"
        lines.append(
            f"| {r['domain']} | {clay_fit} | {agent_fit} | {r['clay_dir']} vs. {r['agent_dir']} | {r['delta']} |"
        )

    lines.append("\n## What the agent added beyond Clay\n")
    lines.append(
        "Per-row notes on signals the agent produced that a typical Clay table "
        "wouldn't (reasoning quotes, pitch hooks, structured AI posture, evidence-backed disqualifiers).\n"
    )
    for r in rows:
        a = r["agent"]
        if a is None:
            continue
        lines.append(f"### {r['domain']}\n")
        lines.append(f"- **Agent fit**: {a.fit_score}/5  ·  **stage**: {a.stage_guess}  ·  **sector**: {a.sector_guess}  ·  **AI posture**: {a.ai_posture}  ·  **RevOps**: {a.revops_maturity}/5")
        if a.disqualifiers_hit:
            tag = " (overridden)" if a.override_reasoning else ""
            lines.append(f"- **Disqualifiers hit**{tag}: {', '.join(a.disqualifiers_hit)}")
        if a.override_reasoning:
            lines.append(f"- **Override reasoning**: {a.override_reasoning}")
        if a.evidence:
            lines.append("- **Evidence cited by agent**:")
            for e in a.evidence[:4]:
                lines.append(f"  - {e}")
        if a.pitch_hooks:
            lines.append("- **Pitch hooks**:")
            for h in a.pitch_hooks[:3]:
                lines.append(f"  - {h}")
        if a.unresolved_gaps:
            lines.append(f"- **Unresolved gaps after {a.iterations_used} iters**: {', '.join(a.unresolved_gaps)}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clay", type=Path, required=True, help="Clay export CSV path")
    parser.add_argument(
        "--outputs",
        type=Path,
        default=PROJECT_DIR / "outputs",
        help="Directory containing agent <domain>.summary.json files",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=PROJECT_DIR / "outputs" / "comparison.md",
        help="Where to write the markdown scorecard",
    )
    args = parser.parse_args()

    if not args.clay.exists():
        print(f"Clay CSV not found: {args.clay}", file=sys.stderr)
        return 2
    if not args.outputs.exists():
        print(f"Outputs dir not found: {args.outputs}", file=sys.stderr)
        return 2

    clay = load_clay(args.clay)
    if not clay:
        print("Clay CSV had no rows", file=sys.stderr)
        return 2

    report = render(clay, args.outputs)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report)
    print(f"[done] wrote {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
