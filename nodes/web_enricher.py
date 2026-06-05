from typing import Literal

from pydantic import BaseModel, Field

from state import AccountResearchState, Raw
from tools import anthropic_client


def _web_search_tool(max_uses: int) -> dict:
    return {
        "type": "web_search_20250305",
        "name": "web_search",
        "max_uses": max_uses,
    }


class WebFinding(BaseModel):
    topic: Literal["funding", "news", "ai_posture", "exec_signal", "product", "other"]
    summary: str
    source_url: str = ""
    date: str = ""


class WebFindings(BaseModel):
    findings: list[WebFinding] = Field(default_factory=list)


def run(state: AccountResearchState, config: dict, hint: str | None = None) -> dict:
    domain = state["domain"]

    user = (
        f"Research the company at {domain}.\n"
        + (f"Specific hint to prioritize: {hint}\n\n" if hint else "\n")
        + "Run searches and emit the bullet summary as instructed."
    )
    max_uses = config.get("web_enricher", {}).get("max_uses", 3)
    search_text = anthropic_client.call_with_tools(
        model=config["models"]["reasoning"],
        system=anthropic_client.load_prompt("web_enricher_search"),
        user=user,
        tools=[_web_search_tool(max_uses)],
        max_tokens=4096,
    )

    if not search_text:
        print(f"[web_enricher] {domain}: search returned no text")
        return {"raw": Raw(web=[])}

    structured = anthropic_client.call_structured(
        model=config["models"]["cheap"],
        system=anthropic_client.load_prompt("web_enricher_structure"),
        user=search_text,
        schema=WebFindings,
    )
    print(f"[web_enricher] {domain}: {len(structured.findings)} structured findings")
    return {"raw": Raw(web=[f.model_dump() for f in structured.findings])}
