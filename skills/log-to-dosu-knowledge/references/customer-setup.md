# Setup (Dosu MCP required)

This skill mines **local** Cursor / Claude Code / Codex histories and writes
notes to the team's Dosu Library via `write_knowledge`. Logs never
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
# equivalent: npx skills add dosu-ai/dosu-skill -g -s "*" -y
```

Confirm the agent can call `whoami` and sees `write_knowledge`.
OSS-only connections (public libraries only) cannot write team knowledge.
Log-backfill writes use `dosu/log-backfill/<timestamp>`; the server auto-enqueues
notes-upflow for that branch prefix (requires a backend that supports it).

## 2. Run it

In the agent chat (repo open, MCP connected):

> Please bootstrap my knowledge with Dosu.
>
> (also: “Mine my agent logs into Dosu.”)

The agent reads local logs, extracts notes, writes each under
a synthetic `dosu/log-backfill/<UTC-timestamp>` branch (server auto-promotes into
the candidate-topic pipeline — same upflow path as a PR merge), opens the HTML
report (including estimated context savings), then replies with **what was cached**, **expected token savings**, and
the backfill branch (analytics-style: rediscovery/generation cost reused on each
future read).

| Ask | Effect |
|-----|--------|
| “Please bootstrap my knowledge with Dosu” | Default write flow (50 most recent) + open HTML report with estimated context savings |
| _(default)_ / “mine my logs” | Mine the **50 most recent** sessions |
| "last N days" / "past month" | All sessions in that window (`--days N`) |
| "full audit" / "everything" | Every discovered parent session (`--full`) |
| "dry-run" / "don't write" | Same extraction; print the `write_knowledge` payloads (synthetic branch) without writing |
| "PDF" | Print / Save as PDF from the HTML report already opened |
| "detailed token report" | Optional `compare_tokens.py` eval with pasted `read_knowledge` responses |

## 3. Repo / branch for writes

| Field | Value |
|-------|--------|
| `repo` | Literal `git remote get-url origin` |
| `branch` | Synthetic `dosu/log-backfill/<UTC-YYYYMMDD-HHMMSS>` for the whole run |

Writes under `dosu/log-backfill/*` auto-enqueue notes-upflow, so notes enter the
candidate-topic pipeline without needing a real PR merge. Works for any repo
connected to the Library (not just dosu-ai/dosu).

**Do not ask the user how to attribute notes to branches** (main vs per-session
vs checkout). Branch is not a product choice for this skill — it is always the
synthetic backfill name so auto-promote runs.

Host must be github.com / gitlab.com / dev.azure.com unless Dosu has allowlisted
their self-hosted git host.

## 4. Privacy

- Do **not** upload raw session JSONL to Dosu.
- Only lean, redacted note text goes through `write_knowledge`.
- The skill skips secrets, PII, and customer-sensitive dumps.

## 5. Success

`dosu setup` / `dosu skill install` → engineer says “Please bootstrap my knowledge with Dosu” (or “mine my logs”) → notes land
on a synthetic backfill branch and auto-enter the candidate-topic pipeline →
short reply: cached titles + expected savings + backfill branch name.
