import yaml
from pydantic import BaseModel, Field

from state import AccountResearchState, Signals
from tools import anthropic_client


class IcpFitAssessment(BaseModel):
    stage_guess: str
    sector_guess: str
    fit_score: float = Field(ge=0.0, le=5.0)
    evidence: list[str]
    disqualifiers_hit: list[str] = Field(default_factory=list)
    # Hybrid disqualifier policy: when disqualifiers_hit is non-empty AND the
    # agent wants to score fit_score > 1, it MUST populate override_reasoning
    # with the specific signal that outweighs the disqualifier. Empty string
    # means "honor the disqualifier" — fit_score is clamped to <= 1 by code
    # in run() regardless of what the LLM emitted.
    override_reasoning: str = ""
    confidence: float = Field(ge=0.0, le=1.0)


def _serialize_site(site: dict | None, max_chars_per_page: int = 6000) -> str:
    if not site:
        return "(no scraped content)"
    parts = []
    for path, page in site.items():
        md = page.get("markdown") or ""
        if not md:
            err = page.get("error")
            parts.append(f"--- {path} (empty{' — ' + err if err else ''}) ---")
            continue
        truncated = md[:max_chars_per_page]
        suffix = "\n[truncated]" if len(md) > max_chars_per_page else ""
        parts.append(f"--- {path} ---\n{truncated}{suffix}")
    return "\n\n".join(parts)


def _serialize_raw(raw) -> str:
    parts = ["## Site content\n" + _serialize_site(raw.site)]
    if raw.jobs:
        parts.append("## Jobs (extracted)\n```json\n" + str(raw.jobs)[:4000] + "\n```")
    if raw.web:
        parts.append("## Web findings\n```json\n" + str(raw.web)[:4000] + "\n```")
    return "\n\n".join(parts)


def run(state: AccountResearchState, config: dict, hint: str | None = None) -> dict:
    """Score ICP fit from all available raw research (site + jobs + web)."""
    raw = state["raw"]
    research_text = _serialize_raw(raw)

    icp_yaml = yaml.safe_dump(config["icp"], sort_keys=False)
    system = anthropic_client.load_prompt("icp_extractor").replace(
        "{{ICP_YAML}}", icp_yaml
    )
    user = (
        f"Company domain: {state['domain']}\n"
        + (f"Hint: {hint}\n\n" if hint else "\n")
        + f"Research content:\n\n{research_text}"
    )

    assessment = anthropic_client.call_structured(
        model=config["models"]["extractor"],
        system=system,
        user=user,
        schema=IcpFitAssessment,
    )

    # Hybrid disqualifier policy enforcement: if disqualifiers were flagged
    # but no override reasoning was provided, clamp fit_score to <= 1 regardless
    # of what the LLM scored. Stops drift where the model emits a high score
    # while flagging a disqualifier without explicitly owning the override.
    clamped = False
    if assessment.disqualifiers_hit and not assessment.override_reasoning.strip():
        if assessment.fit_score > 1.0:
            clamped = True
            assessment = assessment.model_copy(update={"fit_score": 1.0})

    suffix = " [disqualifier clamp applied]" if clamped else (
        f" [override: {assessment.override_reasoning[:60]}...]"
        if assessment.override_reasoning.strip()
        else ""
    )
    print(
        f"[icp_extractor] {state['domain']}: fit={assessment.fit_score}/5 "
        f"confidence={assessment.confidence:.2f} "
        f"stage={assessment.stage_guess} sector={assessment.sector_guess}{suffix}"
    )
    return {"signals": Signals(icp_fit=assessment.model_dump())}
