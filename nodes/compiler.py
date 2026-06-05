import json

from state import AccountResearchState
from tools import anthropic_client


def run(state: AccountResearchState, config: dict) -> dict:
    """Emit the final markdown brief from collected signals."""
    payload = {
        "company": state["domain"],
        "icp_fit": state["signals"].icp_fit,
        "gtm_maturity": state["signals"].gtm_maturity,
        "critique_summary": {
            "iterations_used": state["critique"].iteration,
            "final_score": state["critique"].score,
            "known_gaps": [g.dimension for g in state["critique"].gaps],
        },
    }
    system = anthropic_client.load_prompt("compiler")
    user = "Payload:\n\n" + json.dumps(payload, indent=2, default=str)

    brief = anthropic_client.call(
        model=config["models"]["reasoning"],
        system=system,
        user=user,
        max_tokens=2048,
    )
    print(f"[compiler] {state['domain']}: brief ready ({len(brief)} chars)")
    return {"brief": brief}
