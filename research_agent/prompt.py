"""Builds the agent's system prompt from the markdown workflow.

`workflow/research-report.md` is the single source of truth for how the research
workflow behaves — the same file Claude Code reads. This module loads it at
runtime and appends an adapter block that rewrites the few Claude-Code-only
references so they make sense on the Messages API surface.
"""

from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_FILE = REPO_ROOT / "workflow" / "research-report.md"
STYLE_FILE = REPO_ROOT / "CLAUDE.md"

# The workflow references three things that only exist inside Claude Code.
# Rather than fork the file, we restate them for this surface.
ADAPTER = """
---

# Surface notes (you are running as a standalone Python agent, not inside Claude Code)

The workflow above was written for Claude Code. Three of its references do not
apply here — use these instead:

- **`AskUserQuestion`** — you do not ask questions directly. Phase 1 scoping
  happens in a separate call, and the user's answers are supplied to you below
  under "Scope". Treat them as already gathered.
- **"Keep running notes in the scratchpad"** — there is no scratchpad. Track
  claim → source URL → date → reliability in your reasoning as you go, then
  reflect it in the finished report.
- **`depth: deep` → "one parallel subagent per sub-question"** — you have no
  subagents. Deliver the same depth by running more searches per sub-question
  and fetching more primary sources.

Your tools are `web_search` and `web_fetch` (both run server-side) and
`write_report`. `write_report` is the **only** way to save the report — it
writes into `/output` for you. Do not print the report to the transcript
instead of calling it, and do not call it more than once.

After `write_report` returns, reply with a short plain-text handoff (Phase 5):
the source count, and anything thin, single-sourced, or otherwise weak.

**Today's date is {today}.** Use it for the `*Researched ...*` line and for
judging what counts as recent — you have no other clock.

**Fetch before you cite.** A search snippet is not a source. Every URL in your
Sources list must be one you actually called `web_fetch` on and read. If you
cannot fetch a page, drop it — do not cite it from its snippet. `write_report`
enforces this and will reject a report that cites more pages than you read.
"""


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(
            f"Expected {path.relative_to(REPO_ROOT)} to exist — the agent reads it "
            "as its system prompt. Run from the repo, or restore the file."
        )
    return path.read_text(encoding="utf-8")


def build_system_prompt() -> str:
    """The full workflow text plus project style rules and the surface adapter."""
    return "\n".join(
        [
            _read(WORKFLOW_FILE),
            "\n---\n\n# Project context and style rules (from CLAUDE.md)\n",
            _read(STYLE_FILE),
            ADAPTER.format(today=date.today().isoformat()),
        ]
    )


def overrides_block(depth: str | None, length: int | None) -> str:
    """Render CLI flags as the `Overrides` the workflow already understands."""
    lines = []
    if depth:
        lines.append(f"- `depth: {depth}`")
    if length:
        lines.append(f"- `length: {length}`")
    if not lines:
        return ""
    return "Overrides for this run:\n" + "\n".join(lines)
