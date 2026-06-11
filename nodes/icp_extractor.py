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


# peer-gtm-vendor is only coherent when the model itself classified the company
# as sales/GTM tooling. Haiku reliably gets sector_guess right, then sometimes
# misapplies the flag to any "platform vendor" (observed 2026-06-11: Retool,
# sector "internal tools / low-code platform", flagged as a GTM peer despite the
# prompt naming Retool as the counter-example). Code strips the incoherent flag.
GTM_SECTOR_MARKERS = (
    "sales", "gtm", "go-to-market", "revenue", "sdr", "outbound",
    "abm", "enrichment", "prospecting", "revops",
)


def _strip_incoherent_peer_vendor_flag(assessment: "IcpFitAssessment") -> tuple["IcpFitAssessment", bool]:
    if "peer-gtm-vendor" not in assessment.disqualifiers_hit:
        return assessment, False
    sector = assessment.sector_guess.lower()
    if any(marker in sector for marker in GTM_SECTOR_MARKERS):
        return assessment, False
    remaining = [d for d in assessment.disqualifiers_hit if d != "peer-gtm-vendor"]
    return assessment.model_copy(update={"disqualifiers_hit": remaining}), True


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

    assessment, flag_stripped = _strip_incoherent_peer_vendor_flag(assessment)
    if flag_stripped:
        # The score was reasoned under the wrongly-held flag, so one corrective
        # re-call with the contradiction named. Single retry, capped here in
        # code — if the model flags again, strip again and accept its score.
        print(
            f"[icp_extractor] {state['domain']}: stripped peer-gtm-vendor flag — "
            f"sector_guess '{assessment.sector_guess}' has no GTM marker; re-assessing once"
        )
        correction = (
            "\n\nCORRECTION (rule enforcement): you flagged peer-gtm-vendor, but you "
            f"classified the sector as '{assessment.sector_guess}' — which is not "
            "sales/GTM tooling, so the flag does not apply by definition. Re-assess "
            "WITHOUT peer-gtm-vendor: this company is a potential BUYER of GTM "
            "Engineering services for its own sales motion. Score against the ICP "
            "on the buyer-side evidence."
        )
        assessment = anthropic_client.call_structured(
            model=config["models"]["extractor"],
            system=system,
            user=user + correction,
            schema=IcpFitAssessment,
        )
        assessment, _ = _strip_incoherent_peer_vendor_flag(assessment)

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
