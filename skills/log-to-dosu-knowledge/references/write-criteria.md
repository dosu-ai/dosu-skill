# write_knowledge criteria (for log extraction)

Use these rules when deciding whether a fact from an agent transcript should be
saved to the **connected Dosu Library**.

**Default skill mode auto-writes** every leftover conclusion that passes this
filter. Skip junk (summaries, speculation, secrets, one-file-obvious).
**Under-extracting leftover conclusions is the failure mode.** Do not require
a human approval table unless the user asked for dry-run.

## Write when the log reveals

- A **decision and its rationale** (chose A over B because …)
- A **non-obvious constraint** (API, schema, RLS, feature flag, deploy quirk)
- A **gotcha** that caused real rediscovery cost (race, silent failure, wrong table)
- **Intentional behavior** that looks like a bug but is by design
- A **local repro / ops trick** teammates will need again (ports, env, emulator)
- A **2-line diagnosis** that still answers a leftover user question

## Do not write

- Task or PR summaries, progress updates, “what we did today”
- Test results, lint output, CI green/red snapshots
- Obvious facts readable from a single file without investigation
- Speculation or unverified hypotheses
- Secrets, tokens, PII, customer-sensitive data
- Duplicates of notes already in the Library (from this run’s `read_knowledge` — current Library, not historical transcript reads)

## Title / content shape

- **title**: noun phrase topic (`page_version UniqueViolation race`), not a sentence
- **content**: self-contained observation in plain language; include file/path pointers when useful
- **plain_english** / **how_found**: report helpers (plain-English takeaway and what work found it). Not sent to write_knowledge.
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
2. A 2-line diagnosis still writes — do not require several rediscovery tool
   calls before a leftover conclusion counts.
3. One note per distinct fact; do not dump an entire transcript into one write.
4. Walk **every user turn** in order; do not stop at the last tangent.
5. Assume first-time logs: extract every leftover conclusion that passes the
   filter. Skip duplicates already in this run’s candidate list, and notes
   already in the Library.

## Token attribution

One note per fact. `approx_rediscovery_tokens` is the **cost to learn THIS
fact** — tokens spent arriving at it (question + retrieval + thinking + the
conclusion). That is Decant context + planning + other. Write/Edit and
mutating shell do not count — a note cannot save implementation tokens.
If it took 100k to learn the fact, a future read saves 100k. No session
share, no equal split, no cap. Omit (0) if the stretch is unknown.

Cap `investigation_lines` to THIS fact’s first relevant question through its
conclusion. If `compare_tokens` returns > ~12k, tighten to the last 2–4
conclusion lines (do not attribute a SQL/log dump to every nearby fact).
Measure with:

```
python3 compare_tokens.py --from-digest /tmp/digest-<id>.json --lines START-END
```

## Miner miss-mode (agent instructions)

Do not say "durable" to the customer. Follow these so harvests do not under-count:

1. One note per assistant conclusion, not one per transcript — do not stop at the last tangent.
2. Do not skip a digest because the first message looks like a report / Sentry / SQL paste.
3. Always merge pending (`generate_report.py --pending`) before the report.
4. Bootstrap-only sessions are excluded from the default 50 by the parser; skip them if they appear.
5. If the Library already has the page (this run's `read_knowledge`), skip the write; status `already_in_library` is OK.
6. The HTML baseline is inventory `learning_tokens`, not `effective_tokens` or `context_tokens` alone.
7. Cap dumps: `investigation_lines` is THIS fact only; if `compare_tokens` is
   > ~12k, tighten to the last 2–4 conclusion lines — do not attribute a
   SQL/log dump to every nearby fact.
