from typing import Annotated, Literal, Optional
from pydantic import BaseModel, Field
from typing_extensions import TypedDict


TargetNode = Literal[
    "scraper",
    "web_enricher",
    "icp_extractor",
    "gtm_maturity",
]


class Gap(BaseModel):
    dimension: str
    target_node: TargetNode
    hint: str
    severity: float = Field(ge=0.0, le=1.0)


class Critique(BaseModel):
    score: Optional[float] = None
    gaps: list[Gap] = Field(default_factory=list)
    iteration: int = 0
    history: list[dict] = Field(default_factory=list)


class Raw(BaseModel):
    site: Optional[dict] = None
    jobs: list[dict] = Field(default_factory=list)
    web: list[dict] = Field(default_factory=list)


class Signals(BaseModel):
    icp_fit: Optional[dict] = None
    gtm_maturity: Optional[dict] = None


def merge_raw(a: Optional[Raw], b: Optional[Raw]) -> Raw:
    """Reducer for parallel writes to state['raw'].

    Each research node writes to a different sub-field (scraper→site,
    jobs_reader→jobs, web_enricher→web). When two nodes return Raw in the
    same super-step, take the non-empty value from each side per field.
    Initial reduce against an undefined left side (a=None) returns b.
    """
    if a is None:
        return b or Raw()
    if b is None:
        return a
    return Raw(
        site=b.site if b.site is not None else a.site,
        jobs=b.jobs if b.jobs else a.jobs,
        web=b.web if b.web else a.web,
    )


def merge_signals(a: Optional[Signals], b: Optional[Signals]) -> Signals:
    """Reducer for parallel writes to state['signals'].

    icp_extractor writes icp_fit; gtm_maturity writes gtm_maturity. When both
    return Signals in the same super-step, take the non-None value per field.
    """
    if a is None:
        return b or Signals()
    if b is None:
        return a
    return Signals(
        icp_fit=b.icp_fit if b.icp_fit is not None else a.icp_fit,
        gtm_maturity=b.gtm_maturity if b.gtm_maturity is not None else a.gtm_maturity,
    )


class AccountResearchState(TypedDict, total=False):
    domain: str
    raw: Annotated[Raw, merge_raw]
    signals: Annotated[Signals, merge_signals]
    critique: Critique
    brief: Optional[str]


def init_state(domain: str) -> AccountResearchState:
    return AccountResearchState(
        domain=domain,
        raw=Raw(),
        signals=Signals(),
        critique=Critique(),
        brief=None,
    )
