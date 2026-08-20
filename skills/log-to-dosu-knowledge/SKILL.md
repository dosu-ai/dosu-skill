---
name: log-to-dosu-knowledge
description: >-
  Read Cursor / Claude Code / Codex agent logs and call write_knowledge for each
  note found. Default auto-writes then reports what was cached and
  expected token savings (analytics-style: rediscovery/generation cost reused on
  each future read), and opens the HTML report. Dry-run lists the exact write_knowledge payloads (title,
  content, repo, branch) without writing. Use when the user says "Please bootstrap my knowledge with Dosu",
  "bootstrap agent knowledge", "/bootstrap-agent-knowledge", "log to dosu
  knowledge", "mine my sessions into Dosu", "backfill branch notes from my agent
  logs", "save my agent logs to Dosu", or wants a one-shot pass over local
  histories.
---

# Log → Dosu Knowledge

**Product (keep it this simple):**

This skill is for **first-time users** whose logs predate Dosu.

1. Read local agent logs
2. Decide what to write (not the user’s prompt — the *answer/gotcha*)
3. Write each under a synthetic `dosu/log-backfill/<UTC-timestamp>` branch
   (server auto-enqueues notes-upflow for that prefix — same path as a PR merge)
4. Tell the user **what was cached**, **expected token savings**, and the backfill branch
5. Open the HTML report (`generate_report.py --open`), including **Estimated context savings**

**Dry-run:** same extraction, but **do not** call `write_knowledge`. Output is
only the list of calls you *would* make (with the synthetic branch filled in).

Requires a Dosu MCP connection with `write_knowledge`. Writes must use
`dosu/log-backfill/<timestamp>` so they auto-promote; do **not** fall back to
the current checkout branch (those notes stay stranded until a real PR merges).

## Do not ask (non-negotiable)

**Never** use AskUserQuestion / multiple-choice / “three scope decisions” for
this skill. Especially never ask:

- How notes should be attributed to branches (main / per-session / etc.)
- Note granularity / consolidation policy
- How far back to harvest (unless the user already asked and was ambiguous)

Fixed defaults — just run:

| Decision | Default |
|----------|---------|
| Time / volume | 50 most recent parent sessions |
| Branch on every `write_knowledge` | One `BACKFILL_BRANCH=dosu/log-backfill/<UTC-timestamp>` for the whole run |
| Granularity | One note per assistant conclusion (many notes per long chat). Consolidate only when it is the same fact. |

The MCP tool schema saying “use `git branch --show-current`” does **not** apply
here. Override it. Do not ask the user which branch to use. Inform them of
`BACKFILL_BRANCH` in one line after `whoami`, then continue.

- Setup: [references/customer-setup.md](references/customer-setup.md)
- What to write: [references/write-criteria.md](references/write-criteria.md)
- Log paths: [references/history-locations.md](references/history-locations.md)

## What a write looks like

Each note is one MCP call. Args are exactly:

| Arg | Meaning |
|-----|---------|
| `title` | Noun-phrase topic (`Slack PostgREST 1000-row channel picker cap`) |
| `content` | Self-contained fact a future agent needs |
| `repo` | Literal `git remote get-url origin` |
| `branch` | Synthetic `dosu/log-backfill/<UTC-YYYYMMDD-HHMMSS>` for the whole run |
| `tags` | Optional, e.g. `["from-agent-log", "cursor"]` |

**Wrong:** using the user’s first message as `title`, or treating inventory rows as the notes.

**Right:** after reading a digest, extract the conclusion, e.g.

```
title:   Slack PostgREST 1000-row channel picker cap
content: slackChannel.getAll used an unbounded PostgREST select; hosted
         PostgREST silently returns ≤1000 rows so large workspaces miss
         channels that exist in slack.channel. Page the query.
repo:    git@github.com:acme/api.git
branch:  dosu/log-backfill/20260810-220015
```

## Workflow

```
Progress:
- [ ] 0. whoami + REPO/BACKFILL_BRANCH/SKILL_DIR
- [ ] 1. Inventory (find sessions worth mining — internal)
- [ ] 2. Digest those sessions
- [ ] 3. Build the write_knowledge payload list (+ rediscovery token estimate)
- [ ] 4a. Default: write on BACKFILL_BRANCH (auto-promotes) → open HTML report (with estimated context savings) → reply
- [ ] 4b. Dry-run: print the payload list → stop (no writes, no finalize)
```

### Step 0 — Target

```bash
SKILL_DIR="$(find .claude/skills .cursor/skills .agents/skills \
  -type d -name 'log-to-dosu-knowledge' 2>/dev/null | head -1)"
REPO="$(git remote get-url origin)"
BACKFILL_BRANCH="dosu/log-backfill/$(date -u +%Y%m%d-%H%M%S)"
test -f "$SKILL_DIR/scripts/parse_agent_logs.py"
```

Call `whoami`. Confirm `write_knowledge` is available. One line to the user
which Library will receive notes and the `BACKFILL_BRANCH` for this run
(informational only — not a question). If whoami returns an internal target field,
do not repeat that word to the user — use the Library / org name. Never write
log-backfill notes to the checkout branch. Do not pause for branch / date-range
/ granularity choices.

### Step 1 — Inventory (internal)

Default scope is the **50 most recent** parent sessions. Override when the user asks:

| User says | Flags |
|-----------|--------|
| (default) | _(none — 50 most recent)_ |
| "last N days" / "past month" | `--days 30` (all sessions in that window) |
| "full audit" / "everything" | `--full` |
| "top N" / "N most recent" | `--limit N` |

```bash
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" \
  --out /tmp/dosu-log-inventory.json
# examples:
#   ... --days 30 --out /tmp/dosu-log-inventory.json
#   ... --full --out /tmp/dosu-log-inventory.json
#   ... --limit 100 --out /tmp/dosu-log-inventory.json
```

Use every parent session in that inventory. Rank is only for order (highest `candidate_score` first). **Do not** show user prompts or inventory rows as the result.

### Step 2 — Digest

```bash
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" \
  --digest <id> --json > /tmp/digest-<id>.json
```

Digest **every** mineable parent session in the inventory. After each digest,
walk **every user turn** in order (not just the last). A long investigation
(100k+ `learning_tokens`, 50+ user turns) should yield many notes, not 1–2.

Skip only empty/trivial chats (no real user query). Prefer parent chats over
`subagents/`. Do not skip a digest because the first message looks like a
report / Sentry / SQL paste.

### Step 3 — Build the write list

For each user turn that got an assistant conclusion passing
[write-criteria.md](references/write-criteria.md), append a payload.
**Do not treat “I already wrote one note from this transcript” as done.**

```json
{
  "title": "…",
  "content": "…",
  "repo": "<$REPO>",
  "branch": "<$BACKFILL_BRANCH>",
  "tags": ["from-agent-log", "cursor"],
  "transcript_id": "<source session id>",
  "approx_rediscovery_tokens": 12000,
  "investigation_lines": "128-131",
  "plain_english": "…",
  "how_found": "…"
}
```

`plain_english` is a 1–2 sentence reword of the idea for a teammate (no function/table soup). Report-only — omit from `write_knowledge` like `approx_rediscovery_tokens`.
`how_found` says what work found it (reads, SQL, Logfire, code paths), not a session-share token formula. Report-only — omit from `write_knowledge`.
`investigation_lines` is the same START-END passed to `compare_tokens.py --from-digest --lines`. Required on every candidate or the HTML **Work to learn this** expander is blank. Report-only — omit from `write_knowledge`.

Use the same `BACKFILL_BRANCH` for every candidate in the run. Do **not** use
the checkout branch or a per-log branch name.

`approx_rediscovery_tokens` is the cost to learn THIS fact: tokens spent
arriving at it (question + retrieval + thinking + the conclusion). Includes
Decant context + planning + other. Excludes Write/Edit/mutating shell — a
note cannot save implementation tokens. 100k learned → 100k saved. No cap.
No session share.

Mark digest lines from the first relevant question/tool through the
conclusion for THIS fact only. Measure with:

```
python3 "$SKILL_DIR/scripts/compare_tokens.py" \
  --from-digest /tmp/digest-<id>.json --lines START-END
```

Put `approx_rediscovery_tokens` from that JSON on the payload. Omit the
field only when the stretch cannot be identified — never invent a session
share, never use context-bucket only, never split a session budget.

Skip secrets/PII, task summaries, speculation, obvious one-file facts.

Write the full list to `/tmp/dosu-log-candidates.json` as
`{ "candidates": [ …payloads… ] }` so dry-run, savings summary, and HTML share
one shape.

### Step 4a — Default: write + savings

For each payload, call MCP `write_knowledge` with `title` / `content` / `repo` /
`branch` / `tags` (omit helper fields like `approx_rediscovery_tokens`, `plain_english`, `how_found`, and `investigation_lines`). Every
write must use `BACKFILL_BRANCH`. The server auto-enqueues notes-upflow for
`dosu/log-backfill/*` (same step as a PR merge) — no separate promote call.

If MCP write is unavailable:

```bash
python3 "$SKILL_DIR/scripts/pending_knowledge.py" append \
  --repo "$REPO" --branch "$BACKFILL_BRANCH" \
  --title "…" --content "…" \
  --tags from-agent-log,pending-sync
```

Then compute the default user-facing summary:

```bash
python3 "$SKILL_DIR/scripts/summarize_savings.py" \
  --candidates /tmp/dosu-log-candidates.json
```

**That stdout is the default reply**, plus one line that notes were written on
`BACKFILL_BRANCH` and entered the candidate-topic pipeline. Shape:

```
Cached N notes:
1. <title>
2. <title>

Expected savings: ~Y tokens per future agent read
(same model as analytics: rediscovery/generation cost reused on each hit)

Wrote on dosu/log-backfill/<UTC-YYYYMMDD-HHMMSS> (auto-promoted into the candidate-topic pipeline).
```

Do **not** stop at “Saved N notes” without the savings line.

Then always open the HTML report (not opt-in). Estimated context savings is filled from each note's `approx_rediscovery_tokens` (omit the field only when the investigation stretch cannot be identified — never invent a session share).

The reporter assumes notes were written; pass `--dry-run` only if generating HTML without write_knowledge.

```bash
python3 "$SKILL_DIR/scripts/generate_report.py" \
  --inventory /tmp/dosu-log-inventory.json \
  --candidates /tmp/dosu-log-candidates.json \
  --pending .dosu/pending-knowledge.jsonl \
  --digest-dir /tmp \
  --org-name "…" --repo "$REPO" --branch "$BACKFILL_BRANCH" \
  --out /tmp/dosu-knowledge-report.html --open
```

Call `finalize_session_knowledge` once with write receipt ids if that tool exists.

### Step 4b — Dry-run (when user asks)

**Do not** call `write_knowledge`. Still set `BACKFILL_BRANCH` and include it on
every listed payload. Reply with the payload list, e.g.:

```
Dry-run — would call write_knowledge N times:

1. title: …
   content: …
   repo: …  branch: …
   approx_rediscovery_tokens: …

2. title: …
   content: …
   repo: …  branch: …
   approx_rediscovery_tokens: …
```

That list **is** the dry-run output. Not session prompts. Not inventory scores.
Optionally append the same `summarize_savings.py` block (expected savings if
these were written). If you open the HTML on a dry-run, pass `--dry-run`.

## Opt-in extras

| User says | Behavior |
|-----------|----------|
| "PDF" | Print / Save as PDF from the HTML report already opened |
| "detailed token report" | Optional `compare_tokens.py` eval with pasted `read_knowledge` responses (overrides the default estimate) |

## Miner miss-mode (do not say this word to the customer)

Agent instructions so a harvest does not under-count:

1. One note per assistant conclusion, not one per transcript — do not stop at the last tangent.
2. Do not skip a digest because the first message looks like a report / Sentry / SQL paste.
3. Always merge pending (`generate_report.py --pending`) before the report.
4. Bootstrap-only sessions are excluded from the default 50 by the parser; skip them if they appear.
5. If the Library already has the page (this run's `read_knowledge`), skip the write; status `already_in_library` is OK.
6. The HTML baseline is inventory `learning_tokens`, not `effective_tokens` or `context_tokens` alone.
7. Every candidate must include `investigation_lines` and the report must be generated with `--digest-dir /tmp` (digests left on disk). Without both, **Work to learn this** is blank — `how_found` is not a substitute.

## Guardrails

- Default **writes** on `dosu/log-backfill/*` (server auto-promotes) and always
  includes **expected token savings**, **opens the HTML report**, and fills **Estimated context savings** on that report.
- Never write log-backfill notes to the current checkout branch.
- Never ask how to attribute notes to branches — always `BACKFILL_BRANCH`.
- Never invent a scope questionnaire; use the defaults unless the user already
  specified overrides in their message.
- Dry-run only when asked (no write).
- Never write secrets / PII / raw log dumps.
- One note per `write_knowledge` call; keep notes lean.
- User-facing output is always about **notes** (written or proposed) + savings,
  never raw prompts.

## Quick examples

- "Please bootstrap my knowledge with Dosu." → write on backfill branch (auto-promotes) + cached titles + expected savings + open HTML report (with estimated context savings).
- "Mine my agent logs into Dosu." → same default write flow.
- "Dry-run log to dosu knowledge." → list of `write_knowledge` payloads (synthetic branch) only.
