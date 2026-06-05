from urllib.parse import urljoin

from state import AccountResearchState, Raw
from tools import firecrawl_client

# jobs_reader is invoked inline at the end of this node, not as a separate
# LangGraph node. See the docstring in run() below for the rationale.
from nodes import jobs_reader  # noqa: E402  (deliberate post-state-import)


def _normalize_root(domain: str) -> str:
    if domain.startswith(("http://", "https://")):
        return domain.rstrip("/")
    return f"https://{domain.rstrip('/')}"


def _extract_extra_paths(hint: str) -> list[str]:
    """Pull '/path' tokens out of a critic hint so a re-fire can target them."""
    if not hint:
        return []
    candidates = [tok.strip(".,;:'\"`)") for tok in hint.split() if tok.startswith("/")]
    return [p for p in candidates if 1 < len(p) < 60]


def run(state: AccountResearchState, config: dict, hint: str | None = None) -> dict:
    """Scrape the configured pages on the target domain AND extract structured
    jobs from the scraped careers page in the same super-step.

    The scrape + jobs-extract are combined into a single LangGraph node because
    LangGraph's default channels fire whenever a predecessor produces, not when
    ALL predecessors complete. If scraper and jobs_reader were separate nodes
    on different super-steps, the downstream extractors would fire twice — once
    after scraper, once after jobs_reader's later write reaches them. Collapsing
    them into one node ensures a single extractor pass per iteration.

    Trade-off: a critic gap targeting "jobs re-extraction only" must re-fire
    this whole node (re-scrape too). On Firecrawl free tier that's cheap.

    Stores results in state.raw.site as {page_path: {markdown, metadata, error?}}.
    Pages that 404 or fail are kept with an `error` key so downstream nodes can
    see what was tried. When called with a `hint` containing '/some-path' tokens,
    those paths are appended to the scrape set (deduped).
    """
    domain = state["domain"]
    root = _normalize_root(domain)
    pages = list(config.get("scraper", {}).get("pages_to_attempt", ["/"]))
    for extra in _extract_extra_paths(hint or ""):
        if extra not in pages:
            pages.append(extra)

    site: dict[str, dict] = {}
    for path in pages:
        url = urljoin(root + "/", path.lstrip("/"))
        result = firecrawl_client.scrape(url)
        site[path] = {
            "url": result["url"],
            "markdown": result["markdown"],
            "metadata": result.get("metadata", {}),
        }
        if "error" in result:
            site[path]["error"] = result["error"]

    print(
        f"[scraper] {domain}: scraped {sum(1 for p in site.values() if p['markdown'])}/{len(pages)} pages"
    )

    # Inline jobs extraction. We pass a synthetic state with the freshly scraped
    # site so jobs_reader can parse the careers content. Its returned Raw has
    # only `jobs` populated. We merge site + jobs here so the single Raw write
    # carries both fields in one super-step.
    synthetic = {**state, "raw": Raw(site=site)}
    jobs_result = jobs_reader.run(synthetic, config=config, hint=hint)
    jobs = jobs_result.get("raw").jobs if jobs_result.get("raw") else []

    return {"raw": Raw(site=site, jobs=jobs)}
