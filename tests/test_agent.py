"""Tests for the research agent.

No network and no API keys — the loops are driven against fake clients, so the
whole suite runs offline in under a second.
"""

import datetime
import types

import pytest

from research_agent import cli, gemini_loop, loop, prompt, tools
from research_agent.state import Result, Usage

# --- output path guard -----------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["../../etc/passwd.md", "/etc/passwd.md", "sub/dir.md", "Report.md", "no-ext", ""],
)
def test_rejects_paths_outside_output(bad):
    with pytest.raises(tools.ReportPathError):
        tools.resolve_output_path(bad)


def test_accepts_kebab_case_name():
    path = tools.resolve_output_path("state-of-ai-agents-2026.md")
    assert path.parent == tools.OUTPUT_DIR.resolve()


# --- citation gate ---------------------------------------------------------


def test_unread_citations_flags_unfetched_urls():
    md = "[a](https://a.com/x) and [b](https://b.com/y)"
    assert tools.unread_citations(md, set()) == ["https://a.com/x", "https://b.com/y"]
    assert tools.unread_citations(md, {"https://a.com/x"}) == ["https://b.com/y"]


def test_unread_citations_ignores_trailing_slash_and_punctuation():
    md = "see https://a.com/x/, and [b](https://b.com/y)."
    assert tools.unread_citations(md, {"https://a.com/x", "https://b.com/y"}) == []


# --- minimum sources -------------------------------------------------------


@pytest.mark.parametrize(
    "depth,count,ok",
    [("light", 2, False), ("light", 3, True), (None, 4, False), ("deep", 8, True)],
)
def test_minimum_source_floor(depth, count, ok):
    fetched = {f"https://s{i}.com" for i in range(count)}
    assert (tools.insufficient_sources(fetched, depth) is None) is ok


# --- prompt assembly -------------------------------------------------------


def test_system_prompt_carries_workflow_style_and_date():
    text = prompt.build_system_prompt()
    assert "Phase 3 — Research" in text  # from workflow/research-report.md
    assert "jargon-free" in text  # from CLAUDE.md
    assert "Fetch before you cite" in text  # the surface adapter
    assert datetime.date.today().isoformat() in text


def test_overrides_block_renders_only_what_is_set():
    assert prompt.overrides_block(None, None) == ""
    assert "depth: deep" in prompt.overrides_block("deep", None)
    assert "length: 600" in prompt.overrides_block(None, 600)


# --- backend protocol ------------------------------------------------------


@pytest.mark.parametrize("backend", [loop, gemini_loop])
def test_backends_expose_the_same_surface(backend):
    for attr in (
        "LABEL",
        "ENV_VAR",
        "MODEL",
        "MISSING_KEY_MESSAGE",
        "make_client",
        "call_json",
        "run_agent",
        "describe_error",
        "ERRORS",
    ):
        assert hasattr(backend, attr), f"{backend.__name__} missing {attr}"


def test_provider_selection(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend, error = cli.select_backend("auto")
    assert error is None and backend.LABEL == "Gemini"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    backend, _ = cli.select_backend("auto")
    assert backend.LABEL == "Claude"  # Claude wins when both keys exist

    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.delenv("GEMINI_API_KEY")
    backend, error = cli.select_backend("auto")
    assert backend is None and "No API key" in error


# --- the Claude agentic loop, against a fake client ------------------------


class _Block(dict):
    """A content block that allows attribute access, like the SDK's."""

    __getattr__ = dict.get


def _msg(stop_reason, content):
    return types.SimpleNamespace(
        stop_reason=stop_reason,
        content=content,
        usage=types.SimpleNamespace(input_tokens=10, output_tokens=5),
    )


def _drive(monkeypatch, turns, fetched=(), depth="light"):
    """Run loop.run_agent over a scripted list of responses."""
    seq = iter(turns)
    seen = []

    def fake_turn(client, system, messages, on_event, fetched_set):
        seen.append(len(messages))
        fetched_set.update(fetched)
        return next(seq)

    monkeypatch.setattr(loop, "_stream_turn", fake_turn)
    result = loop.run_agent(None, "sys", "go", lambda e: None, depth)
    return result, seen


def test_loop_resumes_pause_turn_and_writes_report(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "OUTPUT_DIR", tmp_path)
    enough = {f"https://s{i}.com" for i in range(3)}
    turns = [
        _msg("pause_turn", [_Block(type="text", text="searching")]),
        _msg(
            "tool_use",
            [
                _Block(
                    type="tool_use",
                    id="t1",
                    name="write_report",
                    input={"filename": "ok-name.md", "markdown": "# hi"},
                )
            ],
        ),
        _msg("end_turn", [_Block(type="text", text="Done. 3 sources.")]),
    ]
    result, seen = _drive(monkeypatch, turns, fetched=enough)

    assert result.report_path and result.report_path.endswith("ok-name.md")
    assert result.text.startswith("Done.")
    assert result.turns == 3
    assert seen == [1, 2, 4]  # pause appended one message, tool call appended two
    assert result.usage.input_tokens == 30


def test_loop_rejects_report_with_too_few_sources(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "OUTPUT_DIR", tmp_path)
    turns = [
        _msg(
            "tool_use",
            [
                _Block(
                    type="tool_use",
                    id="t1",
                    name="write_report",
                    input={"filename": "thin.md", "markdown": "# thin"},
                )
            ],
        ),
        _msg("end_turn", [_Block(type="text", text="stopped")]),
    ]
    result, _ = _drive(monkeypatch, turns, fetched={"https://only-one.com"})
    assert result.report_path is None
    assert not (tmp_path / "thin.md").exists()


def test_loop_stops_at_max_turns(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "OUTPUT_DIR", tmp_path)
    call = _msg(
        "tool_use",
        [_Block(type="tool_use", id="t", name="unknown_tool", input={})],
    )
    monkeypatch.setattr(loop, "MAX_TURNS", 3)
    monkeypatch.setattr(
        loop, "_stream_turn", lambda c, s, m, e, f: call
    )
    result = loop.run_agent(None, "sys", "go", lambda e: None, "light")
    assert result.turns == 3
    assert any("Stopped after" in w for w in result.warnings)


# --- the Gemini tool dispatcher --------------------------------------------


def test_gemini_execute_gates_then_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(tools, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(gemini_loop, "fetch", lambda url: f"text of {url}")
    result, fetched, events = Result(), set(), []

    def run(name, args):
        return gemini_loop._execute(name, args, result, events.append, fetched, "light")

    # too few sources
    out = run("write_report", {"filename": "a.md", "markdown": "# x"})
    assert "at least 3" in out["error"]

    for i in range(3):
        run("web_fetch", {"url": f"https://s{i}.com"})
    assert len(fetched) == 3

    # enough sources, but cites something unread
    out = run("write_report", {"filename": "a.md", "markdown": "[x](https://other.com)"})
    assert "never fetched" in out["error"]

    # clean
    out = run("write_report", {"filename": "a.md", "markdown": "[s0](https://s0.com)"})
    assert out["saved_to"].endswith("a.md")
    assert (tmp_path / "a.md").read_text() == "[s0](https://s0.com)"


def test_gemini_failed_fetch_does_not_count_as_a_source(monkeypatch):
    monkeypatch.setattr(gemini_loop, "fetch", lambda url: "Fetch failed for x: boom")
    fetched = set()
    gemini_loop._execute(
        "web_fetch", {"url": "https://dead.com"}, Result(), lambda e: None, fetched, None
    )
    assert fetched == set()


# --- usage accounting ------------------------------------------------------


def test_usage_reads_both_sdk_shapes():
    usage = Usage()
    usage.add(types.SimpleNamespace(input_tokens=5, output_tokens=2))
    usage.add(types.SimpleNamespace(prompt_token_count=7, candidates_token_count=3))
    assert (usage.input_tokens, usage.output_tokens) == (12, 5)


# --- fetch quality ---------------------------------------------------------


def test_fetch_rejects_thin_pages(monkeypatch):
    from research_agent import webtools

    html = "<html><title>T</title><body><p>short</p></body></html>"
    monkeypatch.setattr(
        webtools.httpx,
        "get",
        lambda *a, **k: types.SimpleNamespace(
            text=html,
            headers={"content-type": "text/html"},
            raise_for_status=lambda: None,
        ),
    )
    assert webtools.fetch("https://x.com").startswith("Fetch failed")


def test_fetch_accepts_substantial_pages(monkeypatch):
    from research_agent import webtools

    body = "<p>" + ("real article content. " * 100) + "</p>"
    html = f"<html><title>T</title><body>{body}</body></html>"
    monkeypatch.setattr(
        webtools.httpx,
        "get",
        lambda *a, **k: types.SimpleNamespace(
            text=html,
            headers={"content-type": "text/html"},
            raise_for_status=lambda: None,
        ),
    )
    out = webtools.fetch("https://x.com")
    assert out.startswith("# T") and "real article content" in out


def test_fetch_refuses_non_http_urls():
    from research_agent import webtools

    assert webtools.fetch("file:///etc/passwd").startswith("Refused")
