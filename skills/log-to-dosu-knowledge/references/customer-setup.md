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
| `log-to-dosu-knowledge` | Mine agent logs → `write_knowledge` → cached notes + expected savings |

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

The agent reads local logs, extracts durable learnings, calls `write_knowledge`
for each, then replies with **what was cached** and **expected token savings**
(analytics-style: rediscovery/generation cost reused on each future read).

| Ask | Effect |
|-----|--------|
| _(default)_ | Mine the **50 most recent** sessions |
| "last N days" / "past month" | All sessions in that window (`--days N`) |
| "full audit" / "everything" | Every discovered parent session (`--full`) |
| "dry-run" / "don't write" | Same extraction; print the `write_knowledge` payloads without writing |
| "HTML report" / "PDF" | Optional shareable HTML of those payloads |
| "detailed token report" | Full baseline-vs-read counterfactual (`compare_tokens.py`) |

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
in their Branch Notes → short reply: cached titles + expected savings.
