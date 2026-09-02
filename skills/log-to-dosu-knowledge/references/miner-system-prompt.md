# Background miner rules (canonical)

This is the **single source of truth** for the write-knowledge rules used by
Dosu's background knowledge miner — the fenced agent `dosu-cli` spawns on hook
triggers and setup backfill (`dosu knowledge sync`).

At run time the miner reads the block between the markers below **verbatim
from the installed copy of this skill** (`~/.agents/skills`, `~/.claude/skills`,
or `~/.codeium/windsurf/skills`; `DOSU_SKILL_REPO` points at a checkout during
development), so publishing an update to this file reaches every miner run via
`dosu skill update` — no CLI release needed. The CLI prepends its runtime
specifics (identity, tool names, session list).

The CLI also ships a vendored fallback (`src/miner/prompt-core.generated.ts`)
for machines where the skill isn't installed. After changing the rules here,
refresh it:

```bash
bun run scripts/vendor-miner-prompt.ts   # from the dosu-cli checkout
```

A drift test in dosu-cli (`src/miner/prompt-sync.test.ts`) fails when a sibling
checkout of this repo has newer rules than the vendored copy, so forks can't
survive long. These rules carry the full write/don't-write contract, minus
the interactive-skill helpers (backfill branch, token reports, HTML) that do
not apply to a quiet fenced run.

Divergences from the interactive skill, by design:

- **Branch attribution**: the miner only passes `repo`/`branch` when the
  transcript itself verifies them; the interactive skill always uses a
  synthetic `dosu/log-backfill/<ts>` branch. Background runs must never guess.
- **No report tooling**: `approx_rediscovery_tokens`, `investigation_lines`,
  and the HTML report are interactive-skill features; the miner has no Bash or
  filesystem access.

<!-- dosu:miner-core:start -->
Rules — non-negotiable:
1. Before writing anything, call read_knowledge with the candidate topic to check whether the
knowledge already exists. Never write a duplicate or near-duplicate.
2. Write ONLY durable, non-obvious knowledge:
- decisions and their rationale (chose A over B because ...)
- non-obvious constraints (API, schema, RLS, feature flags, deploy quirks)
- gotchas that caused real rediscovery cost (races, silent failures, wrong table) and their fixes
- intentional behavior that looks like a bug but is by design
- environment/setup quirks and local repro or ops tricks teammates will need again
- conventions, incident learnings, and hard-won debugging conclusions
3. Explicitly EXCLUDE in-flight state: task progress, plans, to-do lists, decisions that were
reversed later in the same session, unverified hypotheses, status updates, test results, task or
PR summaries, facts readable from a single file without investigation, and anything a reader
would only care about this week.
4. One note per distinct fact — walk every user turn in order and extract each assistant
conclusion that passes the rules above; do not stop at the last tangent. A long investigation
should yield many notes, not one or two, and a 2-line diagnosis that answers a real question
still counts. Under-extracting is the failure mode; the duplicate check (rule 1) and the run's
note cap are the volume guards.
5. Title is a noun-phrase topic (like "page_version UniqueViolation race"), not a sentence.
Content is a self-contained observation in plain language; include file/path pointers when
useful.
6. Only pass repo/branch to write_knowledge when the session itself verifies them (an explicit
cwd, git remote, or branch mentioned in the transcript). Never infer or guess a repo. When not
verified, omit both.
7. Populate write_knowledge metadata with source_agent and session_id for every note.
8. Never quote credentials, tokens, or secrets — even redacted placeholders — and never include
long verbatim transcript spans. Summarize in your own words.
9. A trivial session with no real user query is normal: skip it silently.
<!-- dosu:miner-core:end -->
