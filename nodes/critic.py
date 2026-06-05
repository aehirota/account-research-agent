import json

from pydantic import BaseModel, Field

from state import AccountResearchState, Critique, Gap
from tools import anthropic_client


class CritiqueResult(BaseModel):
    score: float = Field(ge=0.0, le=5.0)
    gaps: list[Gap] = Field(default_factory=list)


def _summarize_state_for_critic(state: AccountResearchState) -> dict:
    raw = state["raw"]
    site = raw.site or {}
    return {
        "domain": state["domain"],
        "iteration": state["critique"].iteration,
        "previous_critique_history": state["critique"].history,
        "scraped_pages": {
            path: {
                "chars": len(page.get("markdown", "")),
                "has_error": "error" in page,
            }
            for path, page in site.items()
        },
        "jobs": raw.jobs,
        "web_findings_count": len(raw.web),
        "web_topics": [f.get("topic") for f in raw.web],
        "signals": {
            "icp_fit": state["signals"].icp_fit,
            "gtm_maturity": state["signals"].gtm_maturity,
        },
    }


def run(state: AccountResearchState, config: dict) -> dict:
    critique = state["critique"]
    payload = _summarize_state_for_critic(state)
    user = (
        "Current iteration: "
        + str(critique.iteration)
        + "\n\nState summary:\n\n"
        + json.dumps(payload, indent=2, default=str)
    )
    result = anthropic_client.call_structured(
        model=config["models"]["reasoning"],
        system=anthropic_client.load_prompt("critic"),
        user=user,
        schema=CritiqueResult,
    )

    new_iteration = critique.iteration + 1
    history = critique.history + [
        {
            "iter": critique.iteration,
            "score": result.score,
            "gap_count": len(result.gaps),
            "gap_targets": [g.target_node for g in result.gaps],
        }
    ]
    updated = Critique(
        score=result.score,
        gaps=result.gaps,
        iteration=new_iteration,
        history=history,
    )
    print(
        f"[critic] {state['domain']}: iter {critique.iteration} -> {new_iteration}, "
        f"score={result.score:.2f}, gaps={len(result.gaps)} "
        f"(targets={[g.target_node for g in result.gaps]})"
    )
    return {"critique": updated}
