# write_knowledge criteria (for log extraction)

Use these rules when deciding whether a fact from an agent transcript should be
saved to the **connected Dosu deployment**.

**Default skill mode auto-writes** notes that pass this filter. Be selective —
junk notes defeat the one-shot UX — but do not require a human approval table
unless the user asked for dry-run.

## Write when the log reveals

- A **decision and its rationale** (chose A over B because …)
- A **non-obvious constraint** (API, schema, RLS, feature flag, deploy quirk)
- A **gotcha** that caused real rediscovery cost (race, silent failure, wrong table)
- **Intentional behavior** that looks like a bug but is by design
- A **local repro / ops trick** teammates will need again (ports, env, emulator)

## Do not write

- Task or PR summaries, progress updates, “what we did today”
- Test results, lint output, CI green/red snapshots
- Obvious facts readable from a single file without investigation
- Speculation or unverified hypotheses
- Secrets, tokens, PII, customer-sensitive data
- Duplicates of notes already returned by `read_knowledge` for the same query

## Title / content shape

- **title**: noun phrase topic (`page_version UniqueViolation race`), not a sentence
- **content**: self-contained observation in plain language; include file/path pointers when useful
- **repo**: literal `git remote get-url origin`
- **branch**: always the run’s synthetic `dosu/log-backfill/<UTC-YYYYMMDD-HHMMSS>` —
  never the checkout branch, never a per-session git branch from the log, and
  never ask the user which branch to attribute to
- **tags**: optional free-form (`sentry`, `race`, `mcp`)

## Good vs bad

Good:

> The OAuth refresh path in `src/auth/tokens.py` silently swallows 401s; any retry logic must re-check token expiry first.

Bad:

> Fixing auth

## Mapping log evidence → note

1. Prefer assistant **conclusions** after investigation over raw tool dumps.
2. Prefer facts that required **≥ several rediscovery tool calls** (Read/Grep/Shell/Logfire/SQL).
3. One note per distinct durable fact; do not dump an entire transcript into one write.
4. If the transcript already called `write_knowledge` with the same title/content, skip (use it for eval only).
