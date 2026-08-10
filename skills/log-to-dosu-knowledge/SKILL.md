---
name: log-to-dosu-knowledge
description: >-
  Read Cursor / Claude Code / Codex agent logs and call write_knowledge for each
  durable learning found. Default auto-writes then reports how many notes were
  saved. Dry-run lists the exact write_knowledge payloads (title, content, repo,
  branch) without writing. Use when the user says "log to dosu knowledge",
  "mine my sessions into Dosu", "backfill branch notes from my agent logs",
  "save my agent logs to Dosu", or wants a one-shot pass over local histories.
---

# Log → Dosu Knowledge

**Product (keep it this simple):**

1. Read local agent logs  
2. Decide durable learnings (not the user’s prompt — the *answer/gotcha* found)  
3. Call `write_knowledge` for each  
4. Tell the user how many were saved  

**Dry-run:** same extraction, but **do not** call `write_knowledge`. Output is
only the list of calls you *would* make.

Requires a Dosu MCP connection with `write_knowledge`.

- Setup: [references/customer-setup.md](references/customer-setup.md)
- What counts as a learning: [references/write-criteria.md](references/write-criteria.md)
- Log paths: [references/history-locations.md](references/history-locations.md)

## What a write looks like

Each learning is one MCP call. Args are exactly:

| Arg | Meaning |
|-----|---------|
| `title` | Noun-phrase topic (`Slack PostgREST 1000-row channel picker cap`) |
| `content` | Self-contained fact a future agent needs |
| `repo` | Literal `git remote get-url origin` |
| `branch` | From the log when known, else current branch |
| `tags` | Optional, e.g. `["from-agent-log", "cursor"]` |

**Wrong (never do this):** using the user’s first message as `title` / treating
inventory “write gaps” as the notes. Gaps are only which *sessions* to open.

**Right:** after reading a digest, extract the durable conclusion, e.g.

```
title:   Slack PostgREST 1000-row channel picker cap
content: slackChannel.getAll used an unbounded PostgREST select; hosted
         PostgREST silently returns ≤1000 rows so large workspaces miss
         channels that exist in slack.channel. Page the query.
repo:    git@github.com:acme/api.git
branch:  main
```

## Workflow

```
Progress:
- [ ] 0. whoami + REPO/BRANCH/SKILL_DIR
- [ ] 1. Inventory (find sessions worth mining — internal)
- [ ] 2. Digest those sessions
- [ ] 3. Build the write_knowledge payload list
- [ ] 4a. Default: call write_knowledge for each → "Saved N notes"
- [ ] 4b. Dry-run: print the payload list → stop (no writes, no finalize)
```

### Step 0 — Target

```bash
SKILL_DIR="$(find .claude/skills .cursor/skills .agents/skills \
  -type d -name 'log-to-dosu-knowledge' 2>/dev/null | head -1)"
REPO="$(git remote get-url origin)"
BRANCH="$(git branch --show-current)"
test -f "$SKILL_DIR/scripts/parse_agent_logs.py"
```

Call `whoami`. One line to the user which deployment will receive notes.

### Step 1 — Inventory (internal)

```bash
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" \
  --limit 40 \
  --out /tmp/dosu-log-inventory.json
```

Use write-gap ids to pick digests. **Do not** show gap prompts as the result.

### Step 2 — Digest

```bash
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" \
  --digest <id> --json > /tmp/digest-<id>.json
```

Top 5–10 gaps unless the user asks for more. Prefer parent chats over `subagents/`.

### Step 3 — Build the write list

For each durable fact per [write-criteria.md](references/write-criteria.md),
append a payload:

```json
{
  "title": "…",
  "content": "…",
  "repo": "<$REPO>",
  "branch": "<$BRANCH or from log>",
  "tags": ["from-agent-log", "cursor"]
}
```

Skip secrets/PII, task summaries, speculation, obvious one-file facts.

Write the full list to `/tmp/dosu-log-candidates.json` as
`{ "candidates": [ …payloads… ] }` so dry-run and HTML share one shape.

### Step 4a — Default: write

For each payload, call MCP `write_knowledge` with those fields.

If MCP write is unavailable:

```bash
python3 "$SKILL_DIR/scripts/pending_knowledge.py" append \
  --repo "$REPO" --branch "$BRANCH" \
  --title "…" --content "…" \
  --tags from-agent-log,pending-sync
```

Then reply:

```
Saved N notes to Dosu:
1. <title>
2. <title>
```

Call `finalize_session_knowledge` once with write receipt ids if that tool exists.

### Step 4b — Dry-run (when user asks)

**Do not** call `write_knowledge`. Reply with the payload list, e.g.:

```
Dry-run — would call write_knowledge N times:

1. title: …
   content: …
   repo: …  branch: …

2. title: …
   content: …
   repo: …  branch: …
```

That list **is** the dry-run output. Not session prompts. Not inventory scores.

## Opt-in extras

| User says | Behavior |
|-----------|----------|
| "HTML report" / "PDF" | Optional shareable summary (`generate_report.py` + `--candidates`) |
| "token savings" | `compare_tokens.py` (extra) |

```bash
python3 "$SKILL_DIR/scripts/generate_report.py" \
  --inventory /tmp/dosu-log-inventory.json \
  --candidates /tmp/dosu-log-candidates.json \
  --org-name "…" --repo "$REPO" --branch "$BRANCH" \
  --out /tmp/dosu-knowledge-report.html --open
```

## Guardrails

- Default **writes**. Dry-run only when asked.
- Never write secrets / PII / raw log dumps.
- One learning per `write_knowledge` call; keep notes lean.
- User-facing output is always about **notes** (written or proposed), never raw prompts.

## Quick examples

- "Mine my agent logs into Dosu." → write + “Saved N notes”.
- "Dry-run log to dosu knowledge." → list of `write_knowledge` payloads only.
