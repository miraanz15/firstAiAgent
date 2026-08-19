# Research AI Agent

Takes a topic, researches it against real sources, and writes a cited markdown
report to `/output` — ending with two ready-to-post X drafts.

There are **three ways to run the same workflow**. All three read the same
instruction file, [workflow/research-report.md](workflow/research-report.md),
so the shape of the output is identical.

| | Claude Code | Python + Gemini | Python + Claude |
|---|---|---|---|
| How | `/research-report <topic>` | `-m research_agent "<topic>"` | same, `--provider claude` |
| Cost | Your Claude Code subscription | Free tier | ~$1–4 a report |
| Needs | Nothing | A Google AI Studio key | An Anthropic key |
| Quality | Best | Usable, fewer sources | Best |
| Good for | Everyday use | Free automation | Automation where quality matters |

---

## Claude Code path (no setup)

```
/research-report small language models on-device
```

Or say *"run the research workflow on small language models on-device"*. Works
from any directory — also installed at `~/.claude/skills/research-report/`.

**Overrides:** `depth: light`, `depth: deep`, `length: 600`,
`skip outline approval`, or your own section list.

---

## Python path

### Setup

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh    # once
uv venv && uv pip install -r requirements.txt
cp .env.example .env                               # add your key(s)
```

`.env` is loaded automatically — no `export` — and is gitignored.

### Running

```bash
# Interactive: scoping questions, then outline approval
.venv/bin/python -m research_agent "small language models on-device"

# Autonomous, for cron
.venv/bin/python -m research_agent "topic" --yes --depth light
```

| Flag | Effect |
|---|---|
| `--provider auto\|claude\|gemini` | Default `auto` — uses whichever key is in `.env` |
| `--yes` / `-y` | Skip scoping questions and outline approval |
| `--depth light\|deep` | ~5 sources, or 20+ |
| `--length N` | Target word count |
| `--quiet` / `-q` | Hide the streamed reasoning |

---

## What to expect from the Gemini free tier

It works, and it is genuinely free, but the limits shape the output:

- **No Google Search grounding.** It returns 429 on a free key, so search runs
  through DuckDuckGo and fetching through plain HTTP — see
  [webtools.py](research_agent/webtools.py).
- **Flash models only.** Pro tiers have no free quota. The backend starts on
  `gemini-3.6-flash` and drops to `gemini-3.1-flash-lite` when a daily cap is
  hit, then stays there for the run.
- **Quota pauses are normal.** Per-minute limits are waited out; daily ones
  trigger the model switch. You will see both in the progress output.
- **Expect 2–5 sources, not 8–15.** Quota, not the workflow, is the binding
  constraint. Reports are honest about it — the source count is printed in the
  handoff.

For a full-depth report, use the Claude Code path.

## How the Python agent works

```
cli.py         five phases: scope -> outline -> research -> write -> handoff
prompt.py      loads workflow/research-report.md + CLAUDE.md as the system prompt
tools.py       write_report, guarded to /output, plus the citation check
webtools.py    client-side search + fetch (Gemini backend)
loop.py        Claude backend — server-side web tools, streaming
gemini_loop.py Gemini backend — client-side tools, quota handling
state.py       shared Usage/Result types
```

Three decisions worth knowing:

- **The loops are hand-written.** Anthropic's SDK `tool_runner` does not
  auto-resume `pause_turn`, which long research turns hit routinely — it would
  exit silently with a truncated report.
- **`write_report` enforces `/output` in code**, rejecting any filename that
  escapes the directory.
- **Citations are checked, not trusted.** A report may only cite URLs the agent
  actually fetched; snippet-only citations are rejected and the model is made to
  go read the page. This caught a real failure on the first live run — 7 sources
  cited, 1 page actually read.

Because `prompt.py` reads the workflow file at runtime, editing
[workflow/research-report.md](workflow/research-report.md) updates every path at
once. The machine-wide Skill copy is separate — mirror edits there.

## Layout

```
workflow/        instruction files (source of truth for agent behaviour)
output/          finished reports
resources/       reference docs
research_agent/  the Python agent
```
