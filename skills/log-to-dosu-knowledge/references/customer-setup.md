# Setup (Dosu MCP required)

This skill mines **local** Cursor / Claude Code / Codex histories and writes
durable notes to the team's Dosu deployment via `write_knowledge`. Logs never
leave the machine; only lean note text is sent.

## 1. Connect Dosu + install skills

```bash
npx @dosu/cli setup
# or: brew install dosu-ai/dosu/dosu && dosu setup
```

Setup connects Dosu MCP and runs `dosu skill install`, which should install
**both**:

| Skill | Purpose |
|-------|---------|
| `dosu` | Use the Dosu platform (ask, docs, threads, …) |
| `log-to-dosu-knowledge` | Mine agent logs → `write_knowledge` → “Saved N notes” |

Standalone:

```bash
npx @dosu/cli skill install
# equivalent: npx skills add dosu-ai/dosu-skill -g -s dosu -s log-to-dosu-knowledge -y
```

Confirm the agent can call `whoami` and sees `write_knowledge`.
OSS-only connections (no deployment) cannot write team knowledge.

Manual MCP URL shape:

```
https://api.dosu.dev/v1/mcp/deployments/<deployment-id>
```

## 2. Run it

In the agent chat (repo open, MCP connected):

> Mine my agent logs into Dosu.

The agent reads local logs, extracts durable learnings, and calls
`write_knowledge` for each. Expected reply: **Saved N notes to Dosu** + titles.

| Ask | Effect |
|-----|--------|
| "dry-run" / "don't write" | Same extraction; print the `write_knowledge` payloads (title/content/repo/branch) without writing |
| "HTML report" / "PDF" | Optional shareable HTML of those payloads |
| "token savings" | Counterfactual eval (opt-in) |

## 3. Repo / branch for writes

| Field | Value |
|-------|--------|
| `repo` | Literal `git remote get-url origin` |
| `branch` | `git branch --show-current` (or branch from the log) |

Host must be github.com / gitlab.com / dev.azure.com unless Dosu has allowlisted
their self-hosted git host.

## 4. Privacy

- Do **not** upload raw session JSONL to Dosu.
- Only lean, redacted note text goes through `write_knowledge`.
- The skill skips secrets, PII, and customer-sensitive dumps.

## 5. Success

`dosu setup` / `dosu skill install` → engineer says “mine my logs” → notes land
in their Branch Notes → short “Saved N notes” summary.
