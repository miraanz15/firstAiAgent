# Project Context
This is my AI agent workspace. I use it for research, content creation, and productivity workflows.

# About Me
I create content about technology. My audience wants practical, jargon-free output.

# Rules - the guardrails
- Always ask clarifying questions before starting a complex task
- Show your plans and steps before executing
- Keep output concise - bullet points over paragraphs
- Save all output files to the /output folder
- Cite sources when doing research

# Active Workflows
Instruction files in /workflow. When I name one, read its file first and follow it exactly.
- **Research Report** — `workflow/research-report.md` — trigger: "run the research workflow on <topic>" → scoped, source-cited report in /output, ending with 2 X post drafts
  - Also installed machine-wide as a Skill at `~/.claude/skills/research-report/SKILL.md`, invocable as `/research-report` from any directory. The two are separate copies — mirror any edit to both.
  - Also runnable standalone as a Python agent: `.venv/bin/python -m research_agent "<topic>"` (see README.md). It loads `workflow/research-report.md` as its system prompt at runtime, so that file stays the single source of truth. Two backends: Gemini (free tier, default — fewer sources per report) and Claude (`--provider claude`, per-token billing, best quality). Default to the Claude Code path unless I ask for automation.

# Project Structure
/workflow - instruction files
/output - finished deliverables
/resources - reference docs
/research_agent - standalone Python agent (same workflow, API-billed)