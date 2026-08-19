"""Client-side web search and fetch.

The Anthropic backend uses Anthropic's server-side web tools. Gemini's
equivalent — Google Search grounding — has no free-tier quota, so this module
supplies the same two capabilities for free: DuckDuckGo for search (no key
required) and a plain HTTP fetch with HTML stripped to text.
"""

import time

import httpx
from bs4 import BeautifulSoup
from ddgs import DDGS

USER_AGENT = "Mozilla/5.0 (compatible; research-agent/1.0)"
FETCH_TIMEOUT = 20.0
MAX_CHARS = 6_000
# Deliberately tight. Gemini's free tier is limited by tokens-per-minute, and
# full-length pages exhaust it within a few fetches. 6K chars is enough to pull
# the substance and citations out of a typical article.

MIN_USEFUL_CHARS = 800
# Paywalls, cookie walls and JS-rendered pages return a few hundred characters
# of boilerplate. Treated as a source, they let the agent pad its count with
# pages it never really read.


SEARCH_ATTEMPTS = 3


def search(query: str, max_results: int = 8) -> str:
    """Return search hits as a compact numbered list.

    DuckDuckGo rate-limits bursts and answers with an empty list rather than an
    error, so a bare call silently returns nothing mid-run. Retry with backoff
    before believing a zero-result answer.
    """
    hits = []
    for attempt in range(SEARCH_ATTEMPTS):
        try:
            hits = DDGS().text(query, max_results=max_results)
        except Exception as exc:  # network/provider failures must not kill the run
            if attempt == SEARCH_ATTEMPTS - 1:
                return f"Search failed: {type(exc).__name__}: {exc}"
            hits = []
        if hits:
            break
        if attempt < SEARCH_ATTEMPTS - 1:
            time.sleep(2 * (attempt + 1))

    if not hits:
        return (
            f"No results for {query!r} — the search provider may be rate-limiting. "
            "Try a differently-worded query."
        )

    lines = []
    for i, hit in enumerate(hits, 1):
        lines.append(
            f"{i}. {hit.get('title', '(untitled)')}\n"
            f"   {hit.get('href', '')}\n"
            f"   {(hit.get('body') or '')[:300]}"
        )
    return "\n".join(lines)


def fetch(url: str) -> str:
    """Fetch a page and return readable text, truncated to MAX_CHARS."""
    if not url.startswith(("http://", "https://")):
        return f"Refused: {url!r} is not an http(s) URL."
    try:
        response = httpx.get(
            url,
            timeout=FETCH_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        return f"Fetch failed for {url}: {type(exc).__name__}: {exc}"

    if "html" not in response.headers.get("content-type", ""):
        return response.text[:MAX_CHARS]

    soup = BeautifulSoup(response.text, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()
    text = "\n".join(
        line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
    )

    if len(text) < MIN_USEFUL_CHARS:
        return (
            f"Fetch failed for {url}: page returned only {len(text)} characters "
            "(likely a paywall, consent wall, or JavaScript-rendered page). "
            "It does not count as a source — find another."
        )

    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    body = text[:MAX_CHARS]
    truncated = "\n\n[truncated]" if len(text) > MAX_CHARS else ""
    return f"# {title}\nSource: {url}\n\n{body}{truncated}"
