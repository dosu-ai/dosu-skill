# Classification

One `outcome` per extracted call. Mechanical `hint` from the extractor wins unless the transcript contradicts it.

The story is **relevance to the question and solution**, not whether the agent uniquely cited the result in code. If Dosu returned on-topic information, that is a highlight even if the agent also grepped or read files afterward.

## Outcomes

| outcome | Bucket | When |
|---------|--------|------|
| `relevant` | highlight | The returned information was **relevant to the question and solution** — it matched the task, even if the agent did not uniquely quote it downstream. |
| `off_topic` | no effect | A result came back, but it was **not** about the question or solution (unrelated notes, wrong topic). |
| `empty` | no effect | Result is “No knowledge found” (or equivalent empty XML). Note if that empty result was useful (e.g. harvest dedup). |
| `rejected` | no effect | User declined the tool at a permission prompt. |
| `overflow` | waste | Result dumped to a sidecar / truncated file the agent never opened. |
| `error` | waste | Parameter, repo-URL, or server error that burned the call. A later retry can still be `relevant`. |
| `distracting` | waste / failure | Result was misleading or got in the way: wrong API, stale guidance, or it crowded out the real answer. |

Do **not** use `unused`. Legacy findings with `outcome=unused` are treated as `relevant` in the report (on-topic returns that were not uniquely cited).

Highlights = `relevant`. Failures = `distracting` (plus overflow/error in the waste bar).

## Evidence

**relevant** (default when a non-empty result is on-topic)
- The query and the result are about the same problem the user asked.
- A later edit, command, or answer uses a fact from the result.
- The agent still explores code, but the Dosu result was about that same work.
- The agent skips rediscovery because the result already covered it.

**off_topic**
- The result is a generic index, unrelated branch notes, or a different product area than the question.
- Nothing in the result could have helped this task.

**distracting**
- The agent follows a Dosu fact that the current codebase contradicts, then has to reverse.
- The result is huge / off-topic and the agent spends turns summarizing it instead of the task.
- The agent says the knowledge was wrong, stale, or not applicable and it cost work.

Do not mark `relevant` just because `read_knowledge` was called. Do not mark `off_topic` just because the agent also used Grep/Read. Do not mark `distracting` just because the result was long.

## Card copy (highlights and failures)

- **task** — what the user asked / the agent was doing (noun phrase, no pasted prompt)
- **knowledge** — what Dosu returned that mattered (or misled): name the source titles, one or two sentences, never the raw tool payload
- **impact** — what it had to do with the question and solution (or what it cost)

Skip secrets, emails, tokens, and raw user prompts.
