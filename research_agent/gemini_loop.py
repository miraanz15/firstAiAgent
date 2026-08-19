"""Gemini backend — same workflow, free-tier friendly.

Differences from the Anthropic backend, all forced by what a free AI Studio key
actually allows:

- **No Google Search grounding.** It returns 429 on every model with a free key,
  so search and fetch run client-side via `webtools` (DuckDuckGo + httpx).
- **Flash models only.** Pro-tier models also 429 on the free tier.
- Automatic function calling is disabled — we drive the loop ourselves, the same
  way the Anthropic backend does, so behaviour stays comparable.
"""

import json
import os
import time

from google import genai
from google.genai import types

from .state import Result, Usage
from .tools import (
    ReportPathError,
    insufficient_sources,
    unread_citations,
    write_report,
)
from .webtools import fetch, search

MODEL = "gemini-3.6-flash"  # 3.7-flash 503s frequently; pro tiers have no free quota
FALLBACK_MODEL = "gemini-3.1-flash-lite"
MAX_TURNS = 16  # more than Anthropic's: each search/fetch is its own round trip
MAX_OUTPUT_TOKENS = 32_000
RETRY_503 = 3
RETRY_429 = 2  # then fall back a model tier rather than waiting on a daily cap
QUOTA_BACKOFF = 35  # free-tier limits are per-minute, so waiting clears them

LABEL = "Gemini"
ENV_VAR = "GEMINI_API_KEY"
MISSING_KEY_MESSAGE = (
    "GEMINI_API_KEY is not set.\n"
    "  1. Get a key: https://aistudio.google.com/apikey\n"
    "  2. cp .env.example .env, and add GEMINI_API_KEY=... to it\n"
    "  3. Re-run — .env is picked up automatically."
)

_NO_AFC = types.AutomaticFunctionCallingConfig(disable=True)

TOOLS = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="web_search",
            description=(
                "Search the web. Returns titles, URLs and snippets. Use several "
                "differently-phrased queries per sub-question."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={"query": types.Schema(type="STRING")},
                required=["query"],
            ),
        ),
        types.FunctionDeclaration(
            name="web_fetch",
            description=(
                "Fetch a URL and return its readable text. Use this on the "
                "sources that matter — do not build the report from snippets."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={"url": types.Schema(type="STRING")},
                required=["url"],
            ),
        ),
        types.FunctionDeclaration(
            name="write_report",
            description=(
                "Save the finished report. The only way to deliver it — call "
                "exactly once, with the complete markdown."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "filename": types.Schema(
                        type="STRING",
                        description="Kebab-case, ending .md, no directories.",
                    ),
                    "markdown": types.Schema(type="STRING"),
                },
                required=["filename", "markdown"],
            ),
        ),
    ]
)


def make_client():
    return genai.Client(api_key=os.environ[ENV_VAR])


_active_model: str | None = None
# Once a model is exhausted for the day it stays exhausted. Remembering the
# fallback avoids re-paying the retry wait on every subsequent turn.


def _demote(on_event, reason: str) -> str:
    global _active_model
    _active_model = FALLBACK_MODEL
    if on_event:
        on_event(("info", f"{reason} — switching to {FALLBACK_MODEL} for the rest of the run"))
    return FALLBACK_MODEL


def _generate(client, model: str, contents, config, on_event=None):
    """One call, absorbing the two failures the free tier throws constantly.

    503s are transient overload — retry, then drop a model tier. 429s are either
    the per-minute token quota (clears if we wait) or the daily cap (never
    does), so wait twice and then demote for the rest of the run.
    """
    model = _active_model or model
    server_errors = 0
    quota_errors = 0

    while True:
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config
            )
        except genai.errors.ServerError:
            server_errors += 1
            if server_errors >= RETRY_503:
                if model != FALLBACK_MODEL:
                    model = _demote(on_event, f"{model} unavailable")
                    server_errors = 0
                    continue
                raise
            time.sleep(2 * server_errors)
        except genai.errors.ClientError as exc:
            if "RESOURCE_EXHAUSTED" not in str(exc):
                raise
            quota_errors += 1
            # A per-minute limit clears if we wait; a daily one never will. Wait
            # twice, then assume it's daily and drop to a model that still has
            # quota rather than failing the run.
            if quota_errors > RETRY_429:
                if model != FALLBACK_MODEL:
                    model = _demote(on_event, f"{model} out of quota")
                    quota_errors = 0
                    continue
                raise
            if on_event:
                on_event(("info", f"quota hit — waiting {QUOTA_BACKOFF}s"))
            time.sleep(QUOTA_BACKOFF)


def call_json(client, system: str, prompt: str, schema: dict, usage: Usage) -> dict:
    """One non-agentic call returning JSON matching `schema`."""
    response = _generate(
        client,
        MODEL,
        prompt,
        types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            response_mime_type="application/json",
            response_json_schema=schema,
            automatic_function_calling=_NO_AFC,
        ),
    )
    usage.add(response.usage_metadata)
    return json.loads(response.text)


def _execute(
    name: str, args: dict, result: Result, on_event, fetched: set, depth: str | None
) -> dict:
    """Run one function call. Returns the payload Gemini expects back."""
    if name == "web_search":
        output = search(args.get("query", ""))
        on_event(("result", f"{output.count('http')} hit(s)"))
        return {"results": output}
    if name == "web_fetch":
        url = args.get("url", "")
        content = fetch(url)
        if not content.startswith(("Fetch failed", "Refused:")):
            fetched.add(url)
        on_event(("result", f"{len(content):,} chars"))
        return {"content": content}
    if name == "write_report":
        markdown = args.get("markdown", "")
        thin = insufficient_sources(fetched, depth)
        if thin:
            on_event(("error", f"rejected: only {len(fetched)} source(s) read"))
            return {"error": thin}
        unread = unread_citations(markdown, fetched)
        if unread:
            on_event(("error", f"rejected: {len(unread)} unread citation(s)"))
            return {
                "error": (
                    "Rejected — these URLs are cited but were never fetched: "
                    + ", ".join(unread[:8])
                    + ". Call web_fetch on each one you want to cite, then "
                    "rewrite the report using only what you actually read."
                )
            }
        try:
            path = write_report(args.get("filename", ""), markdown)
        except ReportPathError as exc:
            return {"error": f"Refused: {exc}. Retry with a plain kebab-case .md name."}
        except OSError as exc:
            return {"error": f"Could not write the file: {exc}"}
        result.report_path = path
        return {"saved_to": path}
    return {"error": f"Unknown tool: {name}"}


def run_agent(
    client, system: str, user_content: str, on_event, depth: str | None = None
) -> Result:
    """Research and write the report."""
    history = [types.Content(role="user", parts=[types.Part(text=user_content)])]
    result = Result()
    fetched: set[str] = set()  # URLs actually read, gating what may be cited
    config = types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        tools=[TOOLS],
        automatic_function_calling=_NO_AFC,
    )

    for turn in range(1, MAX_TURNS + 1):
        result.turns = turn
        response = _generate(client, MODEL, history, config, on_event)
        result.usage.add(response.usage_metadata)

        candidate = response.candidates[0]
        parts = candidate.content.parts or []
        calls = [p.function_call for p in parts if p.function_call]

        for part in parts:
            if part.text and part.text.strip():
                on_event(("thinking", part.text.strip()[:400]))

        if not calls:
            result.text = response.text or ""
            return result

        history.append(candidate.content)

        # All responses go back in one Content, mirroring the Anthropic backend.
        replies = []
        for call in calls:
            args = dict(call.args or {})
            brief = args.get("query") or args.get("url") or args.get("filename") or ""
            on_event(("call", call.name, str(brief)[:90]))
            payload = _execute(call.name, args, result, on_event, fetched, depth)
            replies.append(
                types.Part.from_function_response(name=call.name, response=payload)
            )
        history.append(types.Content(role="user", parts=replies))

    result.warnings.append(f"Stopped after {MAX_TURNS} turns without a clean finish.")
    return result


def describe_error(exc: Exception) -> str:
    text = str(exc)
    if "RESOURCE_EXHAUSTED" in text or text.startswith("429"):
        return (
            "Gemini free-tier quota exhausted. Wait for the daily reset, or "
            "enable billing at https://aistudio.google.com."
        )
    if text.startswith("503"):
        return "Gemini is overloaded right now. Try again in a minute."
    if "API_KEY_INVALID" in text or text.startswith("401"):
        return "Gemini rejected the key — check GEMINI_API_KEY in .env."
    return text


ERRORS = (genai.errors.APIError,)
