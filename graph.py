from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from nodes import (
    compiler,
    critic,
    gtm_maturity,
    icp_extractor,
    scraper,
    web_enricher,
)
from state import AccountResearchState

# Research-layer nodes the orchestrator can dispatch to. scraper combines
# scrape + jobs extraction in one node (see nodes/scraper.py docstring) so
# that both research branches feed into extractors in the same super-step
# and the extractors fire exactly once per iteration.
COLD_START_DISPATCH = ["scraper", "web_enricher"]
RESEARCH_NODES = ["scraper", "web_enricher"]
EXTRACTOR_NODES = ["icp_extractor", "gtm_maturity"]


def _hint_for(state: AccountResearchState, node_name: str) -> str | None:
    """Return the hint from the first critique gap targeting this node, if any."""
    critique = state.get("critique")
    if not critique or not critique.gaps:
        return None
    for gap in critique.gaps:
        if gap.target_node == node_name:
            return gap.hint
    return None


def _wrap_with_hint(node_run, node_name: str, config: dict):
    def fn(state):
        return node_run(state, config=config, hint=_hint_for(state, node_name))
    return fn


def _wrap_plain(node_run, config: dict):
    def fn(state):
        return node_run(state, config=config)
    return fn


def orchestrator(state: AccountResearchState) -> dict:
    """No-op routing hub. The conditional edge does the real work."""
    crit = state.get("critique")
    iteration = crit.iteration if crit else 0
    score = crit.score if crit else None
    print(f"[orchestrator] iter={iteration} score={score}")
    return {}


def build(config: dict):
    """Day 2 graph: parallel research + critic loop with conditional gap routing.

    Flow:
        START -> orchestrator
        orchestrator -[conditional Send]-> any combo of:
            {scraper, jobs_reader, web_enricher} (research re-fire)
            OR {icp_extractor, gtm_maturity} (extractor-only re-fire)
            OR {compiler} (done)
        each research node -> icp_extractor + gtm_maturity (sync barrier)
        each extractor -> critic (sync barrier)
        critic increments iteration, then -> orchestrator
        compiler -> END

    The critic emits structured Gap objects with target_node. The orchestrator's
    routing function reads gaps and dispatches only the nodes that own them.
    Cold start (iter 0) fires all three research nodes.
    """
    g = StateGraph(AccountResearchState)

    g.add_node("orchestrator", orchestrator)
    g.add_node("scraper", _wrap_with_hint(scraper.run, "scraper", config))
    g.add_node("web_enricher", _wrap_with_hint(web_enricher.run, "web_enricher", config))
    g.add_node("icp_extractor", _wrap_with_hint(icp_extractor.run, "icp_extractor", config))
    g.add_node("gtm_maturity", _wrap_with_hint(gtm_maturity.run, "gtm_maturity", config))
    g.add_node("critic", _wrap_plain(critic.run, config))
    g.add_node("compiler", _wrap_plain(compiler.run, config))

    g.add_edge(START, "orchestrator")

    max_iter = config["graph"]["max_iterations"]
    threshold = config["graph"]["critic_pass_threshold"]
    severity_min = config["graph"]["mandatory_gap_severity"]

    def route_after_orchestrator(state):
        crit = state["critique"]

        # Cold start: scraper (will chain into jobs_reader) + web_enricher in parallel
        if crit.iteration == 0:
            return [Send(n, state) for n in COLD_START_DISPATCH]

        # Hit ceiling — emit brief with known gaps
        if crit.iteration >= max_iter:
            return [Send("compiler", state)]

        # Passed quality threshold
        if crit.score is not None and crit.score >= threshold:
            return [Send("compiler", state)]

        mandatory = [g for g in crit.gaps if g.severity >= severity_min]
        if not mandatory:
            return [Send("compiler", state)]

        research_targets = sorted({g.target_node for g in mandatory if g.target_node in RESEARCH_NODES})
        if research_targets:
            return [Send(n, state) for n in research_targets]

        extractor_targets = sorted({g.target_node for g in mandatory if g.target_node in EXTRACTOR_NODES})
        if extractor_targets:
            return [Send(n, state) for n in extractor_targets]

        return [Send("compiler", state)]

    g.add_conditional_edges("orchestrator", route_after_orchestrator)

    # scraper (with inline jobs extraction) + web_enricher both feed extractors
    # in the same super-step, so extractors fire exactly once per iteration.
    for r in RESEARCH_NODES:
        for e in EXTRACTOR_NODES:
            g.add_edge(r, e)
    for e in EXTRACTOR_NODES:
        g.add_edge(e, "critic")

    g.add_edge("critic", "orchestrator")
    g.add_edge("compiler", END)

    return g.compile()
