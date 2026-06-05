from typing import Literal

from pydantic import BaseModel, Field

from state import AccountResearchState, Raw
from tools import anthropic_client


class JobPosting(BaseModel):
    title: str
    department: str
    location: str
    seniority: Literal["junior", "mid", "senior", "lead", "exec", "unknown"]
    summary: str
    gtm_relevant: bool


class JobsExtract(BaseModel):
    roles: list[JobPosting] = Field(default_factory=list)
    total_open: int = 0
    hiring_signals: list[str] = Field(default_factory=list)


# Known limitation: many companies host their careers page on a third-party ATS
# (Greenhouse, Lever, Ashby, Workday) and only link out from /careers. Firecrawl
# returns the link-out page successfully but it contains no actual job content,
# so this node emits zero roles. The critic correctly flags this as a gap.
# Future fix: detect "Greenhouse/Lever/Ashby" link patterns in the scraped page
# and follow them in a second scrape pass.


def _gather_careers_text(site: dict | None) -> str:
    if not site:
        return ""
    careers_paths = ["/careers", "/jobs", "/"]
    parts = []
    for path in careers_paths:
        page = site.get(path)
        if not page or not page.get("markdown"):
            continue
        parts.append(f"--- {path} ---\n{page['markdown'][:12000]}")
    return "\n\n".join(parts) if parts else ""


def run(state: AccountResearchState, config: dict, hint: str | None = None) -> dict:
    raw = state.get("raw") or Raw()
    careers_text = _gather_careers_text(raw.site)
    if not careers_text:
        print(f"[jobs_reader] {state['domain']}: no careers content available")
        return {"raw": Raw(jobs=[])}

    user = (
        f"Company: {state['domain']}\n"
        + (f"Hint: {hint}\n\n" if hint else "\n")
        + f"Careers content:\n\n{careers_text}"
    )
    extract = anthropic_client.call_structured(
        model=config["models"]["cheap"],
        system=anthropic_client.load_prompt("jobs_reader"),
        user=user,
        schema=JobsExtract,
    )
    gtm_count = sum(1 for r in extract.roles if r.gtm_relevant)
    print(
        f"[jobs_reader] {state['domain']}: {len(extract.roles)} roles parsed, "
        f"{gtm_count} GTM-relevant"
    )
    return {"raw": Raw(jobs=[extract.model_dump()])}
