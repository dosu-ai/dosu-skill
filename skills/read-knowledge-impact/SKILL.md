---
name: read-knowledge-impact
description: >-
  Analyze Cursor / Claude Code / Codex agent logs for Dosu MCP read_knowledge
  calls and write an HTML impact report. A highlight is when the returned
  information was relevant to the question and solution (citing it in code
  is not required). A failure is when the information was distracting or
  misleading. Use when the user asks how Dosu has helped, how the agent has
  used Dosu, how you've used Dosu, read_knowledge impact, a knowledge MCP
  audit, wants a trajectory report of read_knowledge, or says run / re-run
  the read-knowledge-impact skill.
---

# read_knowledge impact audit

Create a report on the impact of the read_knowledge Dosu MCP on my agent trajectories.

Please analyze my historic agent sessions over the past 1-month and identify both highlights and failures of the read_knowledge MCP.

A highlight is when the information from the read_knowledge call was relevant to the question and solution. The agent does not have to uniquely cite it in plan or code.
A failure is when the agent found the information distracting or misleading.

For each highlight and failure, format it as
- task -> what the agent was working on
- knowledge -> what information was surfaced
- impact -> what impact it had

Write an HTML report with the results.

## Product

1. Inventory local agent logs (default: past 30 days, all projects). Cursor / Claude / Codex adapters plus a generic JSON/JSONL walker (Devin, Continue, Windsurf, `DOSU_AGENT_LOG_DIRS`, …).
2. Extract every `read_knowledge` call and its tool result
3. Judge each call: was the information **relevant to the question and solution** (not “did the agent uniquely cite it”)
4. Open the HTML report (`generate_impact_report.py --open`)

Do **not** call `write_knowledge`. This skill only reports.

**Fresh run:** always re-extract and re-classify. Ignore `/tmp/rk-calls.json`, `/tmp/rk-findings.json`, and any existing HTML. Do not skip because a previous report exists.

## Do not ask

Never ask which agent, date range, project, or granularity. Defaults:

| Decision | Default |
|----------|---------|
| Window | `--days 30` |
| Projects | `--all-projects` (every workspace on this machine) |
| Sources | cursor, claude, codex, generic |

Override only when the user already said so (“this repo”, “last week”, “Claude only”).

- Classification: [references/classification.md](references/classification.md)

## Workflow

```
Progress:
- [ ] 0. SKILL_DIR (this SKILL.md’s folder)
- [ ] 1. Extract read_knowledge calls (overwrite /tmp)
- [ ] 2. Classify every call from scratch
- [ ] 3. Write findings JSON
- [ ] 4. Open HTML report → short reply
```

### Step 0 — Skill dir

`SKILL_DIR` is the directory that contains **this** `SKILL.md` (the file you are reading now). Confirm the new classifier is loaded:

```bash
test -f "$SKILL_DIR/scripts/extract_read_knowledge.py"
grep -F '"relevant": "Returned information was relevant to the question and solution."' \
  "$SKILL_DIR/scripts/generate_impact_report.py"
grep -F "details class='fold'" "$SKILL_DIR/scripts/generate_impact_report.py"
```

If either grep fails, stop — you have a stale copy. Use the `dosu-skill` checkout at `skills/read-knowledge-impact/`.

Then clear previous artifacts:

```bash
rm -f /tmp/rk-calls.json /tmp/rk-findings.json /tmp/read-knowledge-impact.html
```

### Step 1 — Extract

```bash
# Set DAYS from the user's window BEFORE extract. The HTML reads this number.
#   past day / last 24 hours / today → DAYS=1
#   last week → DAYS=7
#   last N days → DAYS=N
#   unspecified → DAYS=30
DAYS=30
python3 "$SKILL_DIR/scripts/extract_read_knowledge.py" \
  --days "$DAYS" --all-projects \
  --out /tmp/rk-calls.json
```

| User says | Flags |
|-----------|--------|
| (default) | `--days 30 --all-projects` |
| "this project" / "this repo" | `--days 30` (drop `--all-projects`) |
| "last N days" / "past day" / "last 24 hours" | `--days N` (`--days 1` for a day) `--all-projects` |
| "Claude only" | `--sources claude --days 30 --all-projects` |

`--days` filters by **call time** (`called_at` from Cursor `<timestamp>` / Claude `timestamp`), not file mtime. A long chat last-touched today does not count last week's calls.

Each call has `id`, `source`, `transcript_id`, `path`, `query`, `result_preview`, `hint` (`empty` / `overflow` / `error` / `rejected` / `unknown`), and the session’s first user task.

If the extractor prints `calls: 0`, open an empty report anyway and stop.

### Step 2 — Classify

Read [references/classification.md](references/classification.md). Classify **from the transcripts**, not from a previous findings file.

1. Keep mechanical hints (`empty`, `overflow`, `error`, `rejected`) unless the transcript clearly contradicts them.
2. For `hint=unknown`, digest the session around that call (`parse_agent_logs.py --digest <id>` from the sibling `log-to-dosu-knowledge` skill if present, otherwise read the JSONL near the tool_use).
3. Set `outcome` to exactly one of: `relevant`, `off_topic`, `empty`, `rejected`, `overflow`, `error`, `distracting`.
4. Fill `task`, `knowledge`, `impact` for **every** `relevant` and `distracting` call, and for overflow/error when you can see what happened. Complete sentences — never cut a field mid-word. The report folds long copy behind “more”. No raw prompts, no secrets.

`relevant` = the returned information was **relevant to the question and solution**. The agent does not have to uniquely cite it. If they also grepped or read code, still mark `relevant`.

`off_topic` = a result came back but it was not about this question or solution.

`distracting` = the result sent the agent the wrong way, contradicted the codebase, or crowded out the real answer.

Never use `unused`. On-topic returns that were not uniquely quoted are `relevant`.

### Step 3 — Findings file

Write `/tmp/rk-findings.json` as `{ "window": …, "calls": [ … ] }`. Copy `window` from `/tmp/rk-calls.json` unchanged (that is how the HTML knows it was 1 day vs 30). Each call is the extractor row **plus** `outcome` / `task` / `knowledge` / `impact`. Keep extractor fields. Classify **every** call — do not sample.

Do **not** set `outcome_notes`. The Outcomes table copy comes from `generate_impact_report.py`. The relevant row must read exactly: "Returned information was relevant to the question and solution."

### Step 4 — Report

```bash
python3 "$SKILL_DIR/scripts/generate_impact_report.py" \
  --findings /tmp/rk-findings.json \
  --out /tmp/read-knowledge-impact.html --open
```

Reply with the headline numbers (calls, % relevant, highlight count, failure count) and that the HTML is open. Do not paste every card into chat.

## Guardrails

- Never write secrets / PII / raw log dumps into the report.
- Never invent calls that the extractor did not find.
- Never skip a call because the session was a harvest, a subagent, or “already classified.”
- Never reuse a previous `/tmp/rk-findings.json` — always classify this run from the extractor output.
- User-facing output is the HTML report + a short numeric summary.
