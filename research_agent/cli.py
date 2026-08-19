"""Terminal front end — the five phases of the workflow."""

import argparse
import os
import sys

from .prompt import REPO_ROOT, build_system_prompt, overrides_block
from .tools import MIN_SOURCES
from .state import Usage

SCOPE_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "maxItems": 3,
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "suggested_default": {"type": "string"},
                },
                "required": ["question", "suggested_default"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "sub_questions": {"type": "array", "items": {"type": "string"}},
        "sections": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sub_questions", "sections"],
    "additionalProperties": False,
}


# --- terminal output -------------------------------------------------------

_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def dim(text: str) -> str:
    return _c("2", text)


def ask(prompt: str, default: str = "") -> str:
    """input() that survives piped or closed stdin instead of crashing."""
    try:
        return input(prompt).strip() or default
    except EOFError:
        print(dim("(no input — using default)"))
        return default


def bold(text: str) -> str:
    return _c("1", text)


class Printer:
    """Renders progress events from the agentic loop."""

    def __init__(self, quiet: bool = False):
        self.quiet = quiet
        self._mid_thought = False

    def _break_thought(self) -> None:
        if self._mid_thought:
            print()
            self._mid_thought = False

    def __call__(self, event: tuple) -> None:
        kind = event[0]
        if kind == "thinking":
            if self.quiet:
                return
            if not self._mid_thought:
                sys.stdout.write(dim("  · "))
                self._mid_thought = True
            sys.stdout.write(dim(event[1].replace("\n", " ")))
            sys.stdout.flush()
        elif kind == "call":
            self._break_thought()
            name, brief = event[1], event[2]
            print(f"  {bold('→')} {name}" + (f"  {dim(brief)}" if brief else ""))
        elif kind == "result":
            self._break_thought()
            print(dim(f"    {event[1]}"))
        elif kind in ("error", "info"):
            self._break_thought()
            print(dim(f"    ! {event[1]}"))


# --- phases ----------------------------------------------------------------


def phase_scope(backend, client, system: str, topic: str, usage: Usage) -> str:
    """Phase 1 — ask up to 3 clarifying questions, collect the answers."""
    print(bold("\nPhase 1 — Scope"))
    data = backend.call_json(
        client,
        system,
        f"Topic: {topic}\n\n"
        "Following Phase 1 of the workflow, give me at most 3 clarifying "
        "questions — only where my answer would actually change the research. "
        "If the topic is already unambiguous on a dimension, skip it. Include a "
        "sensible default for each question.",
        SCOPE_SCHEMA,
        usage,
    )

    questions = data.get("questions", [])
    if not questions:
        print(dim("  No clarifying questions — the topic is unambiguous."))
        return "The topic needed no clarification."

    answers = []
    for i, q in enumerate(questions, 1):
        default = q["suggested_default"]
        print(f"\n  {i}. {q['question']}")
        print(dim(f"     default: {default}"))
        reply = ask("     > ", default)
        answers.append(f"- {q['question']}\n  {reply}")
    return "\n".join(answers)


def phase_outline(backend, client, system: str, topic: str, scope: str, usage: Usage) -> str:
    """Phase 2 — propose an outline and wait for go-ahead."""
    feedback = ""
    while True:
        print(bold("\nPhase 2 — Outline"))
        data = backend.call_json(
            client,
            system,
            f"Topic: {topic}\n\nScope:\n{scope}\n{feedback}\n\n"
            "Following Phase 2, break this into 4-6 sub-questions that together "
            "answer it, and propose the report's section headings.",
            OUTLINE_SCHEMA,
            usage,
        )

        print("\n  Sub-questions:")
        for q in data["sub_questions"]:
            print(f"    - {q}")
        print("\n  Sections:")
        for s in data["sections"]:
            print(f"    - {s}")

        reply = ask("\n  Enter to approve, or type what to change: ")
        if not reply:
            outline = "\n".join(f"- {q}" for q in data["sub_questions"])
            sections = "\n".join(f"- {s}" for s in data["sections"])
            return f"Approved sub-questions:\n{outline}\n\nApproved sections:\n{sections}"
        feedback = f"\nMy feedback on your previous outline: {reply}"


def phase_research(backend, client, system: str, brief: str, printer: Printer, depth):
    """Phases 3 and 4 — research, then write the report."""
    print(bold("\nPhase 3/4 — Research and write"))
    return backend.run_agent(client, system, brief, printer, depth)


def phase_handoff(result) -> None:
    """Phase 5 — tell the user what was produced and where it's weak."""
    print(bold("\nPhase 5 — Handoff"))
    if result.report_path:
        print(f"  Report: {result.report_path}")
    else:
        print("  No report was written — see the agent's reply below.")
    if result.text:
        print("\n" + result.text.strip() + "\n")
    for warning in result.warnings:
        print(dim(f"  ! {warning}"))
    usage = result.usage
    print(
        dim(
            f"  {result.turns} turn(s) · {usage.input_tokens:,} in / "
            f"{usage.output_tokens:,} out tokens"
        )
    )


# --- entry point -----------------------------------------------------------


def load_dotenv() -> None:
    """Read .env into the environment if present, so no `export` step is needed.

    Deliberately minimal — a KEY=value reader, no python-dotenv dependency.
    Real environment variables always win.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research_agent",
        description="Research a topic and write a source-cited report to /output.",
    )
    parser.add_argument("topic", help="what to research")
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip the scoping questions and outline approval (for automation)",
    )
    parser.add_argument(
        "--depth",
        choices=["light", "deep"],
        help="light: ~5 sources, quick scan. deep: 20+ sources.",
    )
    parser.add_argument("--length", type=int, help="target word count for the body")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="hide the reasoning trickle"
    )
    parser.add_argument(
        "--provider",
        choices=["auto", "claude", "gemini"],
        default="auto",
        help="auto (default) picks whichever key is in .env; claude is preferred "
        "when both are present",
    )
    return parser


def select_backend(choice: str):
    """Resolve --provider to a backend module, or return an error message."""
    from . import gemini_loop, loop

    backends = {"claude": loop, "gemini": gemini_loop}
    if choice != "auto":
        return backends[choice], None

    available = [b for b in (loop, gemini_loop) if os.environ.get(b.ENV_VAR)]
    if not available:
        return None, (
            "No API key found. Add ANTHROPIC_API_KEY or GEMINI_API_KEY to .env "
            "(see .env.example), or use the free Claude Code path: "
            "/research-report <topic>"
        )
    return available[0], None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_dotenv()

    backend, error = select_backend(args.provider)
    if error:
        print(error, file=sys.stderr)
        return 1
    if not os.environ.get(backend.ENV_VAR):
        print(backend.MISSING_KEY_MESSAGE, file=sys.stderr)
        return 1

    try:
        system = build_system_prompt()
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(dim(f"provider: {backend.LABEL} ({backend.MODEL})"))
    client = backend.make_client()
    usage = Usage()
    printer = Printer(quiet=args.quiet)

    try:
        if args.yes:
            scope = "No scoping questions were asked — use the workflow's defaults."
            outline = "No outline approval — proceed straight to research."
        else:
            scope = phase_scope(backend, client, system, args.topic, usage)
            outline = phase_outline(backend, client, system, args.topic, scope, usage)

        brief = "\n\n".join(
            part
            for part in [
                f"Topic: {args.topic}",
                overrides_block(args.depth, args.length),
                f"Scope:\n{scope}",
                outline,
                f"Read at least {MIN_SOURCES.get(args.depth, MIN_SOURCES[None])} "
                "pages with web_fetch before writing — snippets are not sources, "
                "and write_report will reject a report that falls short.",
                "Now run Phases 3-5 of the workflow. Research the topic, then "
                "call `write_report` exactly once with the finished report.",
            ]
            if part
        )

        result = phase_research(backend, client, system, brief, printer, args.depth)
        # Fold the scoping/outline calls into the reported total.
        result.usage.input_tokens += usage.input_tokens
        result.usage.output_tokens += usage.output_tokens
        phase_handoff(result)
        return 0 if result.report_path else 1

    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    except backend.ERRORS as exc:
        print(f"\n{backend.describe_error(exc)}", file=sys.stderr)
        return 1
