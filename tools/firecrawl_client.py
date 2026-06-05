import os
from typing import Optional

from firecrawl import FirecrawlApp

_app: Optional[FirecrawlApp] = None


def app() -> FirecrawlApp:
    global _app
    if _app is None:
        _app = FirecrawlApp(api_key=os.environ["FIRECRAWL_API_KEY"])
    return _app


def scrape(url: str, only_main: bool = True) -> dict:
    """Scrape a single URL, return {url, markdown, metadata}.

    Returns an empty markdown string and an `error` key if the scrape fails;
    callers should treat partial failures as soft (skip page, keep going).
    """
    try:
        result = app().scrape_url(
            url,
            params={
                "formats": ["markdown"],
                "onlyMainContent": only_main,
            },
        )
        return {
            "url": url,
            "markdown": result.get("markdown", "") or "",
            "metadata": result.get("metadata", {}) or {},
        }
    except Exception as e:
        return {"url": url, "markdown": "", "metadata": {}, "error": str(e)}
