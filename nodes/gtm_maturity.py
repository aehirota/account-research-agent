import json
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from state import AccountResearchState, Signals
from tools import anthropic_client


class GtmMaturity(BaseModel):
    funding_stage: str
    funding_evidence: str
    revops_maturity: float = Field(ge=0.0, le=5.0)
    revops_evidence: list[str] = Field(default_factory=list)
    ai_posture: Literal["leader", "adopter", "cautious", "skeptical", "silent"]
    ai_evidence: list[str] = Field(default_factory=list)
    icp_clarity: float = Field(ge=0.0, le=5.0)
    pitch_hooks: list[str] = Field(default_factory=list)
    disqualifiers: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


def _summarize_raw(raw) -> dict:
    """Compact view of raw research for the maturity model."""
    site = raw.site or {}
    site_summary = {
        path: (page.get("markdown", "")[:2500] + ("..." if len(page.get("markdown", "")) > 2500 else ""))
        for path, page in site.items()
        if page.get("markdown")
    }
    return {
        "site_pages": site_summary,
        "jobs": raw.jobs,
        "web_findings": raw.web,
    }


def run(state: AccountResearchState, config: dict, hint: str | None = None) -> dict:
    icp_yaml = yaml.safe_dump(config["icp"], sort_keys=False)
    system = anthropic_client.load_prompt("gtm_maturity").replace("{{ICP_YAML}}", icp_yaml)

    payload = _summarize_raw(state["raw"])
    user = (
        f"Company: {state['domain']}\n"
        + (f"Hint: {hint}\n\n" if hint else "\n")
        + "Research payload:\n\n"
        + json.dumps(payload, indent=2, default=str)
    )

    assessment = anthropic_client.call_structured(
        model=config["models"]["extractor"],
        system=system,
        user=user,
        schema=GtmMaturity,
    )
    print(
        f"[gtm_maturity] {state['domain']}: "
        f"stage={assessment.funding_stage} revops={assessment.revops_maturity}/5 "
        f"ai={assessment.ai_posture} conf={assessment.confidence:.2f}"
    )
    return {"signals": Signals(gtm_maturity=assessment.model_dump())}
