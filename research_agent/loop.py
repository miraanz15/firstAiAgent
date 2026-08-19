"""The agentic loop.

Written by hand rather than using the SDK's beta `tool_runner`: the Python
runner does not auto-resume `pause_turn`, and long web-research turns hit that
routinely — it would exit silently with a truncated report. Owning the loop
also keeps this off a beta API.
"""

import json
import os

import anthropic

from . import MODEL
from .state import Result, Usage
from .tools import (
    TOOLS,
    ReportPathError,
    insufficient_sources,
    unread_citations,
    write_report,
)

MAX_TOKENS = 64_000  # streaming is required at this size to avoid HTTP timeouts
MAX_TURNS = 12  # bounds a runaway loop

THINKING = {"type": "adaptive", "display": "summarized"}
# display defaults to "omitted"; without this a multi-minute research turn
# looks like a frozen terminal.

LABEL = "Claude"
ENV_VAR = "ANTHROPIC_API_KEY"
MISSING_KEY_MESSAGE = (
    "ANTHROPIC_API_KEY is not set.\n"
    "  1. Get a key: https://console.anthropic.com -> API Keys\n"
    "  2. cp .env.example .env, and paste the key into it\n"
    "  3. Re-run — .env is picked up automatically."
)


def make_client():
    return anthropic.Anthropic(api_key=os.environ[ENV_VAR])


def call_json(client, system: str, prompt: str, schema: dict, usage: Usage) -> dict:
    """One non-agentic call returning JSON matching `schema`."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=16_000,
        system=system,
        thinking=THINKING,
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": schema},
        },
        messages=[{"role": "user", "content": prompt}],
    )
    usage.add(response.usage)
    text = next(b.text for b in response.content if b.type == "text")
    return json.loads(text)


def _stream_turn(client, system: str, messages: list, on_event, fetched: set) -> object:
    """Run one turn, reporting progress as it streams. Returns the final message."""
    pending: dict[int, dict] = {}

    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        thinking=THINKING,
        output_config={"effort": "high"},
        tools=TOOLS,
        messages=messages,
    ) as stream:
        for event in stream:
            kind = getattr(event, "type", "")

            if kind == "content_block_start":
                block = event.content_block
                btype = getattr(block, "type", "")
                if btype in ("server_tool_use", "tool_use"):
                    pending[event.index] = {"name": getattr(block, "name", "?"), "json": ""}
                elif btype in ("web_search_tool_result", "web_fetch_tool_result"):
                    on_event(_describe_tool_result(block))

            elif kind == "content_block_delta":
                delta = event.delta
                dtype = getattr(delta, "type", "")
                if dtype == "input_json_delta" and event.index in pending:
                    pending[event.index]["json"] += delta.partial_json
                elif dtype == "thinking_delta":
                    on_event(("thinking", delta.thinking))

            elif kind == "content_block_stop":
                call = pending.pop(event.index, None)
                if call:
                    brief = _brief_input(call["json"])
                    if call["name"] == "web_fetch" and brief.startswith("http"):
                        fetched.add(brief)
                    on_event(("call", call["name"], brief))

        return stream.get_final_message()


def _brief_input(raw_json: str) -> str:
    """A one-line summary of a tool call's input, for the progress line."""
    try:
        data = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        return ""
    for key in ("query", "url", "filename"):
        if key in data:
            return str(data[key])
    return ""


def _describe_tool_result(block) -> tuple:
    """Server-tool errors arrive as HTTP 200 with an error object, not a raised
    exception — and success `content` is a list where an error is an object."""
    content = getattr(block, "content", None)
    if isinstance(content, list):
        return ("result", f"{len(content)} result(s)")
    code = getattr(content, "error_code", None) or "unknown error"
    return ("error", f"tool error: {code}")


def run_agent(
    client, system: str, user_content: str, on_event, depth: str | None = None
) -> Result:
    """Research and write the report. `on_event` receives progress tuples."""
    messages: list = [{"role": "user", "content": user_content}]
    result = Result()
    fetched: set[str] = set()  # server-side fetches, read off the stream

    for turn in range(1, MAX_TURNS + 1):
        result.turns = turn
        response = _stream_turn(client, system, messages, on_event, fetched)
        result.usage.add(response.usage)

        if response.stop_reason == "pause_turn":
            # A server tool hit its iteration limit mid-turn. Append the paused
            # assistant turn and re-send to let it carry on.
            messages.append({"role": "assistant", "content": response.content})
            on_event(("info", "server tool paused — resuming"))
            continue

        if response.stop_reason == "max_tokens":
            result.warnings.append(
                "Response hit max_tokens — the report may be truncated."
            )

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            result.text = "\n".join(b.text for b in response.content if b.type == "text")
            return result

        messages.append({"role": "assistant", "content": response.content})

        # All tool_results go back in a SINGLE user message — splitting them
        # teaches the model to stop making parallel calls.
        tool_results = []
        for block in tool_uses:
            content, is_error = _execute(block, result, fetched, depth)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": content,
                    **({"is_error": True} if is_error else {}),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    result.warnings.append(
        f"Stopped after {MAX_TURNS} turns without a clean finish."
    )
    return result


def _execute(
    block, result: Result, fetched: set, depth: str | None
) -> tuple[str, bool]:
    """Run one client-side tool call. Returns (content, is_error)."""
    if block.name != "write_report":
        return f"Unknown tool: {block.name}", True

    markdown = block.input["markdown"]
    thin = insufficient_sources(fetched, depth)
    if thin:
        return thin, True
    unread = unread_citations(markdown, fetched)
    if unread:
        return (
            "Rejected — these URLs are cited but were never fetched: "
            + ", ".join(unread[:8])
            + ". Fetch each one you want to cite, then rewrite the report "
            "using only what you actually read."
        ), True

    try:
        path = write_report(block.input["filename"], markdown)
    except ReportPathError as exc:
        return f"Refused: {exc}. Retry with a plain kebab-case .md filename.", True
    except OSError as exc:
        return f"Could not write the file: {exc}", True
    result.report_path = path
    return f"Report saved to {path}", False


def describe_error(exc: Exception) -> str:
    """Most-specific-first, so retryable and non-retryable stay distinguishable."""
    if isinstance(exc, anthropic.NotFoundError):
        return f"Model or endpoint not found: {exc}"
    if isinstance(exc, anthropic.RateLimitError):
        return "Rate limited by the API. Wait a moment and re-run."
    if isinstance(exc, anthropic.AuthenticationError):
        return "Authentication failed — check ANTHROPIC_API_KEY."
    if isinstance(exc, anthropic.APIStatusError):
        return f"API error {exc.status_code}: {exc}"
    if isinstance(exc, anthropic.APIConnectionError):
        return f"Could not reach the API: {exc}"
    return str(exc)


ERRORS = (anthropic.APIError,)
