# Agent history locations (Cursor / Claude / Codex)

The inventory script discovers all three by default (`--sources cursor,claude,codex`).
Scoped to the current project cwd unless you pass `--all-projects` or `--dir`.

## Cursor

```
~/.cursor/projects/<encoded-cwd>/agent-transcripts/<uuid>/<uuid>.jsonl
~/.cursor/projects/<encoded-cwd>/agent-transcripts/<uuid>/subagents/<id>.jsonl
```

- Encoding: absolute path with `/` → `-`
  e.g. `/Users/you/work/acme-api` → `Users-you-work-acme-api`
- Format: `{ "role": "user"|"assistant", "message": { "content": [ blocks ] } }`
- Tokens: no usage field → `chars/4` estimate
- Override: `CURSOR_AGENT_TRANSCRIPTS_DIR`

## Claude Code

```
$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/<session-id>.jsonl
$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/sessions/<session-id>.jsonl   # some builds
$CLAUDE_CONFIG_DIR/projects/<encoded-cwd>/subagents/agent-<id>.jsonl
```

- Default config dir: `~/.claude` (override with `CLAUDE_CONFIG_DIR`)
- Encoding: path with non-alphanumerics → `-` (also try slash-only encoding)
- Format: `{ "type": "user"|"assistant"|…, "message": { "content": [...], "usage": {...}, "id": "…" }, "cwd", "gitBranch", "sessionId" }`
- Tokens: `message.usage` **deduped by `message.id`** (streamed blocks repeat usage)
- Retention: often 30 days (`cleanupPeriodDays` in settings)

If `projects/` is missing, Claude Code has not written sessions on this machine yet — Cursor/Codex still inventoriable.

## Codex

```
~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl
~/.codex/session_index.jsonl
```

- Format: envelope lines with `type` ∈ `session_meta` | `response_item` | `event_msg` | `turn_context`
- User prompts: `event_msg.payload.type == "user_message"` (and `response_item` role=user)
- Tools: `response_item.payload.type == "function_call"` (`exec_command`, …)
- Tokens: `event_msg.payload.type == "token_count"` → `info.total_token_usage.total_tokens` (preferred)
- cwd filter: `session_meta.payload.cwd` matched against `--cwd`

## CLI cheatsheet

Set `SKILL_DIR` to the installed skill directory (see SKILL.md), then:

```bash
# Default: 50 most recent parent sessions (current project cwd)
python3 "$SKILL_DIR/scripts/parse_agent_logs.py"

# Past 30 days (all sessions in window)
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" --days 30

# Full audit
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" --full

# Codex only
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" --sources codex

# Claude only, every project
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" --sources claude --all-projects

# Explicit folder (auto-detect per file)
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" --dir ~/exports/agent-logs

# Fixture self-test
python3 "$SKILL_DIR/scripts/parse_agent_logs.py" --self-test

# Pending notes when MCP write is unavailable
python3 "$SKILL_DIR/scripts/pending_knowledge.py" list

# Optional HTML report (only if user asks)
python3 "$SKILL_DIR/scripts/generate_report.py" \
  --inventory /tmp/dosu-log-inventory.json \
  --out /tmp/dosu-knowledge-report.html --open
```
