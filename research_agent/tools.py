"""Tool definitions and the one client-side tool implementation."""

import re
from pathlib import Path

from .prompt import REPO_ROOT

OUTPUT_DIR = REPO_ROOT / "output"

# Server-side tools — these run on Anthropic's infrastructure, so there is no
# function to implement. The _20260209 variants have dynamic filtering built in,
# which is why code_execution is deliberately NOT declared alongside them.
WEB_SEARCH = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 30,
}

WEB_FETCH = {
    "type": "web_fetch_20260209",
    "name": "web_fetch",
    "max_uses": 20,
    "citations": {"enabled": True},
}

WRITE_REPORT = {
    "name": "write_report",
    "description": (
        "Save the finished research report. This is the only way to deliver the "
        "report — call it exactly once, with the complete markdown body."
    ),
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Kebab-case filename ending in .md, derived from the topic. "
                    "No directories — the file always lands in /output."
                ),
            },
            "markdown": {
                "type": "string",
                "description": "The complete report, following the workflow template.",
            },
        },
        "required": ["filename", "markdown"],
        "additionalProperties": False,
    },
}

TOOLS = [WEB_SEARCH, WEB_FETCH, WRITE_REPORT]

_SAFE_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.md$")


class ReportPathError(ValueError):
    """Raised when a proposed filename would escape /output."""


def resolve_output_path(filename: str) -> Path:
    """Map a model-supplied filename onto a path inside /output, or refuse.

    CLAUDE.md requires all output in /output. Enforcing that here rather than
    trusting the prompt means a bad filename fails loudly instead of writing
    somewhere unexpected.
    """
    name = (filename or "").strip()
    if not _SAFE_NAME.match(name):
        raise ReportPathError(
            f"{filename!r} is not a plain kebab-case .md filename "
            "(e.g. 'state-of-ai-agents-2026.md')"
        )

    path = (OUTPUT_DIR / name).resolve()
    if path.parent != OUTPUT_DIR.resolve():
        raise ReportPathError(f"{filename!r} resolves outside /output")
    return path


def write_report(filename: str, markdown: str) -> str:
    """Client tool implementation. Returns the path, for the model's handoff."""
    path = resolve_output_path(filename)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
    return str(path)


_URL = re.compile(r"https?://[^\s)\]>\"']+")

# How many pages must actually be read before a report may be filed, by depth.
# The workflow asks for 8-15 sources; these are floors, not targets, set low
# enough that a rate-limited free tier can still finish.
MIN_SOURCES = {"light": 3, None: 5, "deep": 8}


def insufficient_sources(fetched: set[str], depth: str | None) -> str | None:
    """Refuse a report built on too few pages. Returns an error, or None."""
    required = MIN_SOURCES.get(depth, MIN_SOURCES[None])
    if len(fetched) >= required:
        return None
    return (
        f"Rejected — you have read {len(fetched)} page(s) but this report needs "
        f"at least {required}. Search for more angles and web_fetch the sources "
        "that matter, then write the report."
    )


def unread_citations(markdown: str, fetched: set[str]) -> list[str]:
    """URLs the report cites that were never actually fetched.

    The workflow's rule is that snippets are not sources. Models drift on this
    under context pressure — citing a search result they never opened — so it is
    checked here rather than left to the prompt.
    """
    fetched_norm = {_normalize(u) for u in fetched}
    cited = {_normalize(u) for u in _URL.findall(markdown)}
    return sorted(u for u in cited if u not in fetched_norm)


def _normalize(url: str) -> str:
    """Trailing punctuation and slashes must not make a read URL look unread.

    Order matters and interleaves: a prose URL can end '.../x/,' where stripping
    either class alone leaves the other behind.
    """
    previous = None
    while url != previous:
        previous = url
        url = url.rstrip(".,;:!?)’\"'").rstrip("/")
    return url
