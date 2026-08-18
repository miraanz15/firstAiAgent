# Workflow: Research → Report

Instructions for Claude. Triggered when I say something like *"Run the research workflow on \<topic\>"*.

Goal: take a topic, research it properly, and produce a clean, source-cited markdown report in `/output`.

**Overrides** I can state up front — honour them and skip the matching default:
- `depth: light` — ~5 sources, quick scan
- `depth: deep` — 20+ sources, one parallel subagent per sub-question
- `skip outline approval` — go straight from scoping to research
- `length: <N>` — target word count for the report body, overriding the ~800–1200 default
- a custom section list — use mine instead of the template's

---

## Phase 1 — Scope (always ask, never skip)

Before anything else, ask me clarifying questions with `AskUserQuestion`. Max 3 questions, and only where my answer would actually change the research. Cover:

- **Angle/purpose** — general-audience explainer, decision brief, or technical deep-dive?
- **Audience and length** — default ~800–1200 words, practical and jargon-free
- **Constraints** — recency window (default: prioritise the last 18 months), geography, must-cover subtopics, things to deliberately exclude

If the topic is already unambiguous on one of these, don't ask about it.

## Phase 2 — Outline for approval

1. Break the topic into 4–6 sub-questions that together answer it.
2. Show me those sub-questions plus the proposed section headings as a short bullet list.
3. **Wait for my go-ahead. Do not start searching until I approve.**

## Phase 3 — Research

- Run 2–4 `WebSearch` queries per sub-question, varying the phrasing so you escape a single framing.
- `WebFetch` the sources that actually matter — don't build the report out of search snippets.
- Target **8–15 distinct sources**. Prefer primary sources (papers, official docs, company or government announcements) over aggregator coverage.
- Keep running notes in the scratchpad, one line per claim: `claim → source URL → date → one-line reliability assessment`.
- **Corroboration rule:** any contested, surprising, or numerical claim needs two independent sources. If you only have one, keep it but mark it `(single-sourced)` in the report.
- Note disagreements and gaps explicitly. Do not smooth them over into false consensus.

## Phase 4 — Write the report

Write to `output/<kebab-case-topic>.md` using this template:

```markdown
# <Topic>
*Researched <YYYY-MM-DD> · <N> sources*

## Executive Summary
<2–3 sentence prose overview: what the topic is and what the research concluded>

**Key takeaways**
- 3–5 bullets: the answers, up front

## Background
- What the reader needs before the findings make sense

## Key Findings
### <Sub-question 1>
- Finding, with inline source link
### <Sub-question 2>
- ...

## Disagreements & Open Questions
- Where sources conflict, and what remains unknown

## What This Means
- Practical implications for the reader

## Sources
1. [Title](url) — publisher, date — one line on what it contributed

## Post on X
1. <one-liner — single sentence, no hashtags, no emoji filler>
2. <one-liner — different insight, different framing>
```

**Executive Summary** must stand alone. A reader who reads only this section should get the whole answer — no "see below" references, no unexplained jargon. Write it last, from the finished findings.

**Post on X** is required on every report. Two ready-to-post drafts:
- The first covers the single most surprising or counterintuitive insight from the research; the second covers a different one.
- **One line each — a single sentence.** Aim for under ~150 characters; hard cap 280. If it needs a second sentence to land, the insight isn't sharp enough yet — cut it down, don't add.
- Punchy and opinionated, plain language. Lead with the number or the claim, not the setup.
- No hashtags, no thread numbering, no emoji padding.
- Bold framing is fine; invented facts are not — each must be defensible from the sourced findings above.
- Vary the two (e.g. one a sharp claim, one a concrete number or comparison) so I have a real choice.

**Style** (from `claude.md`): bullets over paragraphs, jargon-free, practical. Every non-obvious factual claim carries an inline link. Label speculation and inference as such — never present them as sourced fact.

## Phase 5 — Hand off

Tell me:
- the file path
- how many sources you used
- anything thin, single-sourced, or otherwise weak, so I know where not to trust the report
