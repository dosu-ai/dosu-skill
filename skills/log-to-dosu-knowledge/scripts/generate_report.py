#!/usr/bin/env python3
"""Generate a shareable HTML report from log-to-dosu-knowledge outputs.

Inputs (any combination; inventory is required):
  --inventory   JSON from parse_agent_logs.py
  --candidates  JSON of proposed/written notes (see schema below)
  --token-report JSON from compare_tokens.py
  --pending     .dosu/pending-knowledge.jsonl (optional)

Output: self-contained HTML with Print / Save as PDF (browser print dialog).

Candidates schema:
{
  "org_name": "Acme",
  "repo": "git@github.com:acme/api.git",
  "branch": "main",
  "summary": "optional one-liner",
  "candidates": [
    {
      "transcript_id": "<id>",
      "source": "cursor|claude|codex",
      "title": "OAuth refresh token expiry",
      "content": "self-contained note body…",
      "approx_rediscovery_tokens": 12000,
      "investigation_lines": "128-131",
      "plain_english": "1–2 sentences, no function/table soup",
      "how_found": "what the agent had to read/trace (Logfire, SQL, Slack catalog, code paths) to land the conclusion",
      "user_query": "why does auth retry loop?",
      "status": "proposed|written|pending"
    }
  ]
}

--candidates is required for a useful "knowledge" section: it must be the list
of write_knowledge payloads (title, content, repo, branch), not session prompts.
If omitted, the report shows inventory stats only and an empty candidates
section — never invents notes from user queries.

Usage:
  python3 generate_report.py --inventory inv.json --candidates c.json --out report.html
  python3 generate_report.py --inventory inv.json --candidates c.json \\
      --token-report tokens.json --out report.html --open
  python3 generate_report.py --inventory inv.json --candidates c.json \\
      --dry-run --out report.html --open
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from activity_buckets import classify_tool

MAX_TRACE_STEPS = 50
PREVIEW_CHARS = 80
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_SQL_DISPLAY = frozenset({"execute_sql", "query_run", "list_tables"})
_LOGS_DISPLAY = frozenset({"query_logs"})
_SKIP_TOOL_NAMES = frozenset({"tool_result"})


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_pending(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not row.get("synced"):
            out.append(row)
    return out


CHARS_PER_TOKEN = 4.0


def effective_tokens(t: dict[str, Any]) -> int:
    return int(t.get("reported_tokens") or t.get("estimated_tokens") or 0)


def token_totals_from_candidates(
    candidates: list[dict[str, Any]],
    inventory: dict[str, Any],
) -> dict[str, Any] | None:
    """Fill Estimated context savings from note rediscovery estimates.

    Same model as summarize_savings.py: replaced ≈ Σ approx_rediscovery_tokens.
    Read cost is chars/4 of title+content (lean note a future read_knowledge returns).
    Baseline is inventory totals.learning_tokens, else the sum of each
    transcript's learning_tokens. Never effective_tokens, never
    context_tokens alone.
    """
    if not candidates:
        return None
    replaced = 0
    has_rediscovery = False
    read_cost = 0
    for c in candidates:
        raw = c.get("approx_rediscovery_tokens")
        if raw is not None:
            has_rediscovery = True
            replaced += max(0, int(raw))
        blob = f"{c.get('title') or ''}\n{c.get('content') or ''}"
        read_cost += int(round(len(blob) / CHARS_PER_TOKEN))
    if not has_rediscovery:
        return None
    baseline = int((inventory.get("totals") or {}).get("learning_tokens") or 0)
    if not baseline:
        baseline = sum(
            int(t.get("learning_tokens") or 0)
            for t in (inventory.get("transcripts") or [])
        )
    saved = max(0, replaced - read_cost)
    pct = round(100.0 * saved / baseline, 1) if baseline else 0.0
    return {
        "baseline_tokens": baseline,
        "replaced_baseline_tokens": replaced,
        "read_knowledge_tokens": read_cost,
        "tokens_saved": saved,
        "pct_saved": pct,
    }


def merge_pending(
    candidates: list[dict[str, Any]], pending: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    titles = {c.get("title") for c in candidates}
    for p in pending:
        if p.get("title") in titles:
            continue
        candidates.append(
            {
                "transcript_id": p.get("transcript_id") or "",
                "source": "pending",
                "title": p.get("title"),
                "content": p.get("content") or "",
                "approx_rediscovery_tokens": None,
                "user_query": "",
                "status": "pending",
            }
        )
    return candidates


def apply_status_defaults(
    candidates: list[dict[str, Any]], *, dry_run: bool
) -> list[dict[str, Any]]:
    """Fill missing/unknown status: proposed on dry-run, written otherwise.

    Existing written/pending/proposed/already_in_library values are kept
    (case-insensitive).
    Always returns copies.
    """
    fallback = "proposed" if dry_run else "written"
    known = {"written", "pending", "proposed", "already_in_library"}
    out: list[dict[str, Any]] = []
    for c in candidates:
        row = dict(c)
        current = (row.get("status") or "").strip().lower()
        if current not in known:
            row["status"] = fallback
        out.append(row)
    return out


def fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}"
    except (TypeError, ValueError):
        return "—"


def esc(s: Any) -> str:
    return html.escape("" if s is None else str(s))


def presentation_copy(c: dict[str, Any]) -> tuple[str, str]:
    """Return (idea, work) for a note card.

    idea is plain_english when set, otherwise content.
    work is how_found when set, otherwise a token-stretch fallback.
    """
    idea = (c.get("plain_english") or "").strip() or (c.get("content") or "").strip()
    how = (c.get("how_found") or "").strip()
    if how:
        return idea, how
    raw = c.get("approx_rediscovery_tokens")
    if raw is None:
        raw = c.get("baseline_tokens")
    try:
        n = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        n = 0
    if n:
        work = (
            f"About {n:,} tokens of reading, searching, and tracing "
            "in this session before the conclusion."
        )
    else:
        work = "Investigation stretch was not measured."
    return idea, work


def parse_line_spec(spec: Any) -> set[int]:
    """Parse '40-88,102,110-115' (or a list of those) into 1-based line numbers."""
    out: set[int] = set()
    if spec is None:
        return out
    if isinstance(spec, (list, tuple, set)):
        raw = ",".join(str(x) for x in spec)
    else:
        raw = str(spec)
    if not raw.strip():
        return out
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                start_s, end_s = part.split("-", 1)
                start, end = int(start_s.strip()), int(end_s.strip())
                if end < start:
                    start, end = end, start
                out.update(range(start, end + 1))
            else:
                out.add(int(part))
        except ValueError:
            continue
    return out


def fallback_line_spec(turns: list[dict[str, Any]]) -> set[int]:
    lines: list[int] = []
    for turn in turns:
        try:
            line = int(turn.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if line > 0:
            lines.append(line)
    if not lines:
        return set()
    return set(range(min(lines), max(lines) + 1))


def load_digest(
    digest_dir: Path | None,
    transcript_id: str,
    cache: dict[str, Any],
) -> dict[str, Any] | None:
    """Load digest-<transcript_id>.json from digest_dir, caching by id."""
    tid = (transcript_id or "").strip()
    if not digest_dir or not tid:
        return None
    if tid in cache:
        return cache[tid]
    path = digest_dir / f"digest-{tid}.json"
    if not path.is_file():
        matches = sorted(digest_dir.glob(f"digest-{tid}*.json"))
        path = matches[0] if matches else path
    if not path.is_file():
        cache[tid] = None
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cache[tid] = None
        return None
    if not isinstance(data, dict):
        cache[tid] = None
        return None
    cache[tid] = data
    return data


def expand_digest_turns(
    turns: list[dict[str, Any]], spec_lines: set[int]
) -> list[dict[str, Any]]:
    """Turns from the last user at/before min_line through max_line."""
    if not turns or not spec_lines:
        return []
    min_line = min(spec_lines)
    max_line = max(spec_lines)
    start = min_line
    for turn in turns:
        try:
            line = int(turn.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if turn.get("role") == "user" and line <= min_line:
            start = line
    out: list[dict[str, Any]] = []
    for turn in turns:
        try:
            line = int(turn.get("line") or 0)
        except (TypeError, ValueError):
            continue
        if start <= line <= max_line:
            out.append(turn)
    return out


def _collapse_preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    cleaned = _TAG_RE.sub(" ", text or "")
    cleaned = _WS_RE.sub(" ", cleaned).strip()
    if len(cleaned) > limit:
        return cleaned[: limit - 1].rstrip() + "…"
    return cleaned


def _turn_text(turn: dict[str, Any]) -> str:
    texts = turn.get("text") or []
    if isinstance(texts, str):
        return texts
    return " ".join(str(t) for t in texts if t)


def _inner_tool_name(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "")
    for key in ("toolName", "tool_name"):
        val = tool.get(key)
        if val:
            return str(val)
    knowledge = tool.get("knowledge")
    if isinstance(knowledge, dict) and knowledge.get("tool"):
        return str(knowledge["tool"])
    if name.startswith("mcp:"):
        return name.split(":", 1)[-1]
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1]
    return name


def display_tool_name(tool: dict[str, Any]) -> str:
    inner = _inner_tool_name(tool)
    wrapper = str(tool.get("name") or "")
    if inner == "GetMcpTools" or wrapper == "GetMcpTools":
        return "MCP schema"
    if inner in _SQL_DISPLAY:
        return "SQL"
    if inner in _LOGS_DISPLAY:
        return "Logs"
    return inner or wrapper or "tool"


def _first_arg_preview(args: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(args, dict):
        return ""
    for key in keys:
        if args.get(key):
            pv = _collapse_preview(str(args[key]))
            if pv:
                return pv
    return ""


def _tool_preview(tool: dict[str, Any], turn_text: str) -> str:
    name = str(tool.get("name") or "")
    inner = _inner_tool_name(tool)

    path = tool.get("path")
    if path:
        raw = str(path)
        leaf = raw.rsplit("/", 1)[-1] if "/" in raw else raw
        pv = _collapse_preview(leaf)
        if pv:
            return pv

    pattern = tool.get("pattern")
    if pattern:
        pv = _collapse_preview(str(pattern))
        if pv:
            return pv

    preview = tool.get("command_preview")
    if preview:
        pv = _collapse_preview(str(preview))
        if pv:
            return pv

    knowledge = tool.get("knowledge")
    if isinstance(knowledge, dict):
        pv = _first_arg_preview(
            knowledge.get("arguments"), ("query", "sql", "command")
        )
        if pv:
            return pv

    pv = _first_arg_preview(
        tool.get("arguments"), ("query", "sql", "command", "description")
    )
    if pv:
        return pv

    tool_name = tool.get("toolName") or tool.get("tool_name")
    server = tool.get("server")
    if tool_name:
        combo = f"{tool_name} · {server}" if server else str(tool_name)
        pv = _collapse_preview(combo)
        if pv:
            return pv

    pv = _collapse_preview(turn_text)
    if pv:
        return pv

    if name == "GetMcpTools" or inner == "GetMcpTools":
        return "Look up available MCP tools"
    if name == "CallMcpTool":
        extra = tool_name or (inner if inner != "CallMcpTool" else "")
        if extra:
            return f"MCP call · {extra}"
        return "MCP call"
    return "No input recorded"


def _classify_digest_tool(tool: dict[str, Any]) -> str:
    name = str(tool.get("name") or "")
    inner = _inner_tool_name(tool)
    inp: dict[str, Any] = {}
    if tool.get("command_preview"):
        inp["command"] = tool["command_preview"]
        inp["cmd"] = tool["command_preview"]
    if inner and inner != name:
        inp["toolName"] = inner
    return classify_tool(name or inner, inp or None)


def _usable_tools(turn: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for tool in turn.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "")
        if name in _SKIP_TOOL_NAMES:
            continue
        out.append(tool)
    return out


def build_trace_steps(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One timeline row per user question, tool call, or reasoning turn."""
    steps: list[dict[str, Any]] = []
    for turn in turns:
        role = turn.get("role") or ""
        try:
            est = int(turn.get("est_tokens") or 0)
        except (TypeError, ValueError):
            est = 0
        text = _turn_text(turn)
        tools = _usable_tools(turn)
        if role == "user":
            steps.append(
                {
                    "kind": "user",
                    "bucket": "other",
                    "label": "Question",
                    "preview": _collapse_preview(text) or "No input recorded",
                    "tokens": est,
                }
            )
            continue
        if tools:
            share = est // len(tools) if tools else 0
            remainder = est - share * len(tools)
            for i, tool in enumerate(tools):
                bucket = _classify_digest_tool(tool)
                tokens = share + (remainder if i == 0 else 0)
                preview = _tool_preview(tool, text)
                if bucket == "code":
                    extra = "implementation (not counted)"
                    preview = f"{preview} · {extra}" if preview else extra
                steps.append(
                    {
                        "kind": "tool",
                        "bucket": bucket,
                        "label": display_tool_name(tool),
                        "preview": preview,
                        "tokens": tokens,
                    }
                )
            continue
        if text:
            steps.append(
                {
                    "kind": "reasoning",
                    "bucket": "other",
                    "label": "Reasoning",
                    "preview": _collapse_preview(text),
                    "tokens": est,
                }
            )
    return steps


def cap_trace_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(steps) <= MAX_TRACE_STEPS:
        return steps
    omitted = len(steps) - MAX_TRACE_STEPS
    return [steps[0], {"omitted": omitted}, *steps[-(MAX_TRACE_STEPS - 1) :]]


def render_trace_html(
    candidate: dict[str, Any],
    digest_dir: Path | None,
    cache: dict[str, Any],
) -> str:
    spec = parse_line_spec(candidate.get("investigation_lines"))
    digest = load_digest(digest_dir, str(candidate.get("transcript_id") or ""), cache)
    if not digest:
        return ""
    if not spec:
        spec = fallback_line_spec(list(digest.get("turns") or []))
    if not spec:
        return ""
    turns = expand_digest_turns(list(digest.get("turns") or []), spec)
    if not turns:
        return ""
    steps = build_trace_steps(turns)
    if not steps:
        return ""

    learning = {"context": 0, "planning": 0, "other": 0}
    tool_counts: Counter[str] = Counter()
    reasoning_n = 0
    for step in steps:
        bucket = step.get("bucket")
        tokens = int(step.get("tokens") or 0)
        if bucket in learning:
            learning[bucket] += tokens
        if step.get("kind") == "reasoning":
            reasoning_n += 1
        elif step.get("kind") == "tool" and bucket != "code":
            tool_counts[str(step.get("label") or "tool")] += 1

    learning_total = sum(learning.values())
    official = candidate.get("approx_rediscovery_tokens")
    try:
        summary_tokens = int(official) if official is not None else learning_total
    except (TypeError, ValueError):
        summary_tokens = learning_total

    bits = [f"{n} {name}" for name, n in tool_counts.most_common(5)]
    if reasoning_n:
        bits.append(f"{reasoning_n} reasoning")
    bits.append(f"~{fmt_int(summary_tokens)} tok")
    summary = "Work to learn this · " + " · ".join(bits)

    learn_sum = learning_total or 1
    bar_spans = []
    for bucket in ("context", "planning", "other"):
        pct = 100.0 * learning[bucket] / learn_sum if learning_total else 0.0
        bar_spans.append(
            f'<span class="tb {bucket}" style="width:{pct:.1f}%"></span>'
        )
    legend_parts = []
    if learning["context"]:
        legend_parts.append(f"Reads &amp; search {fmt_int(learning['context'])}")
    if learning["planning"]:
        legend_parts.append(f"Planning {fmt_int(learning['planning'])}")
    if learning["other"]:
        legend_parts.append(f"Reasoning {fmt_int(learning['other'])}")
    legend = " · ".join(legend_parts)

    rows = [
        '<li class="ts-head" aria-hidden="true">'
        '<span class="chip">Action</span>'
        '<span class="pv">What it did</span>'
        '<span class="tok">Tokens</span>'
        "</li>"
    ]
    for step in cap_trace_steps(steps):
        if step.get("omitted"):
            rows.append(
                '<li class="ts-omit"><span class="chip"></span>'
                f'<span class="pv">{esc(step["omitted"])} earlier steps omitted</span>'
                '<span class="tok"></span></li>'
            )
            continue
        bucket = str(step.get("bucket") or "other")
        if step.get("kind") == "user":
            css = "ts-user"
        else:
            css = {
                "context": "ts-context",
                "planning": "ts-planning",
                "other": "ts-other",
                "code": "ts-code",
            }.get(bucket, "ts-other")
        tok = step.get("tokens")
        tok_html = f'<span class="tok">{esc(fmt_int(tok)) if tok else ""}</span>'
        rows.append(
            f'<li class="{css}"><span class="chip">{esc(step.get("label"))}</span>'
            f'<span class="pv">{esc(step.get("preview"))}</span>{tok_html}</li>'
        )

    return f"""
<details class="trace">
  <summary>{esc(summary)}</summary>
  <div class="trace-bar" title="context / planning / other">
    {"".join(bar_spans)}
  </div>
  <p class="trace-legend">{legend}</p>
  <ol class="trace-steps">
    {"".join(rows)}
  </ol>
</details>
"""


def notes_section_copy(candidates: list[dict[str, Any]]) -> tuple[str, str, str, str]:
    """Heading, lede, footer title, footer body — based on candidate statuses."""
    n = len(candidates)
    written = sum(1 for c in candidates if (c.get("status") or "written").lower() == "written")
    pending = sum(1 for c in candidates if (c.get("status") or "").lower() == "pending")
    already = sum(
        1 for c in candidates if (c.get("status") or "").lower() == "already_in_library"
    )
    proposed = n - written - pending - already
    if n > 0 and already == n:
        return (
            "Notes already in the Library",
            "These pages were already present — no new write_knowledge calls.",
            "Share",
            "Nothing new to write; the Library already had these pages.",
        )
    if n > 0 and written == n:
        return (
            "Notes written to Dosu",
            "",
            "Share",
            "Notes are on the backfill branch and in the candidate-topic pipeline. Print / Save as PDF if you want a copy.",
        )
    if n > 0 and proposed == n:
        return (
            "Proposed write_knowledge calls",
            "Plain-English takeaway plus what it took to find — not the original user prompts.",
            "Next step",
            "Run the skill (without dry-run) so these payloads are written via write_knowledge.",
        )
    if n == 0:
        return (
            "write_knowledge notes",
            "Plain-English takeaway plus what it took to find — not the original user prompts.",
            "Next step",
            "Run the skill so learnings are extracted and written via write_knowledge.",
        )
    bits = []
    if written:
        bits.append(f"{written} written")
    if proposed:
        bits.append(f"{proposed} proposed")
    if pending:
        bits.append(f"{pending} pending")
    if already:
        bits.append(f"{already} already in library")
    return (
        "write_knowledge notes",
        f"{', '.join(bits)} — Plain-English takeaway plus what it took to find — not the original user prompts.",
        "Share",
        "Written notes are in the candidate-topic pipeline. Proposed/pending items still need a write.",
    )


def build_report(
    *,
    inventory: dict[str, Any],
    candidates_doc: dict[str, Any] | None,
    token_report: dict[str, Any] | None,
    pending: list[dict[str, Any]],
    org_name: str | None,
    repo: str | None,
    branch: str | None,
    dry_run: bool = False,
    digest_dir: Path | None = None,
) -> str:
    transcripts = inventory.get("transcripts") or []

    cand_doc = candidates_doc or {}
    candidates = list(cand_doc.get("candidates") or [])
    # Never invent "notes" from inventory user prompts — those are not
    # write_knowledge payloads. Empty list until the agent extracts learnings.
    candidates = merge_pending(candidates, pending)
    candidates = apply_status_defaults(candidates, dry_run=dry_run)

    def _rediscovery_tokens(c: dict[str, Any]) -> int:
        raw = c.get("approx_rediscovery_tokens")
        if raw is None:
            return 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    candidates.sort(key=_rediscovery_tokens, reverse=True)

    org = org_name or cand_doc.get("org_name") or "Your team"
    repo_s = repo or cand_doc.get("repo") or inventory.get("cwd") or "—"
    branch_s = branch or cand_doc.get("branch") or "—"
    summary = cand_doc.get("summary") or (
        "Local agent session logs were mined into Dosu notes so the next task "
        "can reuse them — reducing rediscovery cost."
    )
    generated = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")

    tok = (token_report or {}).get("totals") or {}
    has_token = bool(token_report and tok)
    derived_savings = False
    if not has_token:
        derived = token_totals_from_candidates(candidates, inventory)
        if derived:
            tok = derived
            has_token = True
            derived_savings = True

    def _learning_tokens(t: dict[str, Any]) -> int:
        return int(t.get("learning_tokens") or 0)

    # Heavy sessions (top by cost-to-learn tokens)
    heavy = sorted(transcripts, key=_learning_tokens, reverse=True)[:8]

    notes_heading, notes_lede, footer_title, footer_body = notes_section_copy(candidates)

    digest_cache: dict[str, Any] = {}
    candidate_rows = []
    for i, c in enumerate(candidates, 1):
        idea, work = presentation_copy(c)
        trace_html = render_trace_html(c, digest_dir, digest_cache)
        content = (c.get("content") or "").strip()
        if idea:
            idea_html = f'<p class="idea">{esc(idea)}</p>'
        else:
            idea_html = "<p class='muted'>Extract a lean note before writing to Dosu.</p>"
        work_html = (
            f'<p class="work"><span class="work-label">To find this</span> {esc(work)}</p>'
        )
        if content and content != idea:
            tech_html = (
                '<details class="note-tech"><summary>Technical note</summary>'
                f'<pre class="note-body">{esc(content)}</pre></details>'
            )
        else:
            tech_html = ""
        status = c.get("status") or "written"
        query_html = (
            f'<p class="query"><strong>Trigger:</strong> {esc(c.get("user_query"))}</p>'
            if c.get("user_query")
            else ""
        )
        candidate_rows.append(f"""
<article class="card" id="c-{i}">
  <header>
    <span class="badge status-{esc(status)}">{esc(status)}</span>
    <span class="badge">{esc(c.get("source") or "?")}</span>
    <h3>{esc(c.get("title") or "Untitled")}</h3>
  </header>
  <p class="meta">
    session <code>{esc(c.get("transcript_id") or "—")}</code>
    · rediscovery ~{fmt_int(c.get("approx_rediscovery_tokens") or c.get("baseline_tokens"))} tok
  </p>
  {idea_html}
  {work_html}
  {trace_html}
  {tech_html}
  {query_html}
</article>
""")

    heavy_rows = "".join(f"""<tr>
      <td>{esc(t.get("source"))}</td>
      <td><code>{esc(t.get("transcript_id"))}</code></td>
      <td class="num">{fmt_int(_learning_tokens(t))}</td>
      <td class="num">{fmt_int(t.get("rediscovery_tool_calls"))}</td>
      <td>{esc(((t.get("user_queries") or [""])[0])[:100])}</td>
    </tr>""" for t in heavy)

    token_section = ""
    if has_token:
        pct = tok.get("pct_saved")
        pct_s = f"{pct}%" if pct is not None else "—"
        token_section = f"""
<section>
  <h2>Estimated context savings</h2>
  <p class="lede">Counterfactual: replace rediscovery stretches with a Dosu <code>read_knowledge</code> hit.</p>
  <div class="stats">
    <div class="stat"><div class="label">Baseline (cost to learn)</div><div class="value">{fmt_int(tok.get("baseline_tokens"))}</div></div>
    <div class="stat"><div class="label">Learning replaced</div><div class="value">{fmt_int(tok.get("replaced_baseline_tokens"))}</div></div>
    <div class="stat"><div class="label">Read cost</div><div class="value">{fmt_int(tok.get("read_knowledge_tokens"))}</div></div>
    <div class="stat highlight"><div class="label">Est. tokens saved</div><div class="value">{fmt_int(tok.get("tokens_saved"))} <span class="pct">({esc(pct_s)})</span></div></div>
  </div>
  {"" if derived_savings else '<p class="footnote">' + esc("Relative estimate (host-reported tokens when available, else chars/4). Not a billing invoice. Large unrelated branch-note dumps in read_knowledge can erase savings — lean notes matter.") + "</p>"}
</section>
"""
    else:
        token_section = """
<section>
  <h2>Estimated context savings</h2>
  <p class="muted">No rediscovery estimates on the notes yet — each candidate needs <code>approx_rediscovery_tokens</code> (or pass <code>--token-report</code>).</p>
</section>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Dosu knowledge report — {esc(org)}</title>
<style>
  :root {{
    --ink: #14201c;
    --muted: #5c6b64;
    --line: #d5ddd8;
    --bg: #f4f7f5;
    --card: #ffffff;
    --accent: #0b6e4f;
    --accent-soft: #e3f2eb;
    --warn: #8a5a00;
    --warn-bg: #fff6e5;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
    color: var(--ink);
    background: var(--bg);
    line-height: 1.45;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 2.5rem 1.5rem 4rem; }}
  header.hero {{
    border-bottom: 2px solid var(--ink);
    padding-bottom: 1.25rem;
    margin-bottom: 1.75rem;
  }}
  .eyebrow {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    font-size: 0.72rem;
    color: var(--muted);
    margin: 0 0 0.4rem;
  }}
  h1 {{
    font-size: 2rem;
    font-weight: 600;
    margin: 0 0 0.5rem;
    letter-spacing: -0.02em;
  }}
  h2 {{
    font-size: 1.25rem;
    margin: 2rem 0 0.75rem;
    border-top: 1px solid var(--line);
    padding-top: 1.25rem;
  }}
  h3 {{ margin: 0; font-size: 1.05rem; }}
  .lede {{ color: var(--muted); margin: 0 0 1rem; }}
  .meta-line {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.85rem;
    color: var(--muted);
  }}
  .toolbar {{
    display: flex;
    flex-wrap: wrap;
    gap: 0.6rem;
    margin: 1rem 0 0;
  }}
  button, .btn {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    border: 1px solid var(--ink);
    background: var(--ink);
    color: #fff;
    padding: 0.55rem 0.9rem;
    border-radius: 4px;
    cursor: pointer;
    font-size: 0.9rem;
    text-decoration: none;
    display: inline-block;
  }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
    gap: 0.75rem;
    margin: 1rem 0;
  }}
  .stat {{
    background: var(--card);
    border: 1px solid var(--line);
    padding: 0.85rem 1rem;
    border-radius: 6px;
  }}
  .stat.highlight {{ background: var(--accent-soft); border-color: #b7d8c7; }}
  .stat .label {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
  }}
  .stat .value {{ font-size: 1.35rem; font-weight: 600; margin-top: 0.25rem; }}
  .pct {{ font-size: 0.9rem; font-weight: 500; color: var(--accent); }}
  .chip {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.78rem;
    background: var(--card);
    border: 1px solid var(--line);
    padding: 0.2rem 0.55rem;
    border-radius: 999px;
    margin-right: 0.35rem;
    display: inline-block;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.82rem;
    background: var(--card);
  }}
  th, td {{
    border-bottom: 1px solid var(--line);
    padding: 0.45rem 0.5rem;
    text-align: left;
    vertical-align: top;
  }}
  th {{ color: var(--muted); font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 1rem 1.1rem;
    margin: 0.75rem 0;
  }}
  .card header {{ display: flex; flex-wrap: wrap; gap: 0.4rem; align-items: baseline; }}
  .badge {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    border: 1px solid var(--line);
    padding: 0.1rem 0.4rem;
    border-radius: 3px;
    color: var(--muted);
  }}
  .status-written {{ background: var(--accent-soft); color: var(--accent); border-color: #b7d8c7; }}
  .status-pending {{ background: var(--warn-bg); color: var(--warn); border-color: #efd59a; }}
  .status-proposed {{ background: #eef1f0; }}
  .status-already_in_library {{ background: #eef1f0; }}
  .note-body {{
    white-space: pre-wrap;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.86rem;
    background: var(--bg);
    border-radius: 4px;
    padding: 0.75rem;
    overflow-x: auto;
  }}
  .idea {{
    font-size: 1.05rem;
    color: var(--ink);
    margin: 0.5rem 0 0.4rem;
  }}
  .work {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.85rem;
    color: var(--muted);
    margin: 0.25rem 0 0.5rem;
  }}
  .work-label {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    margin-right: 0.4rem;
  }}
  .note-tech {{
    margin-top: 0.6rem;
  }}
  .note-tech summary {{
    cursor: pointer;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.82rem;
    color: var(--muted);
    margin-top: 0.5rem;
  }}
  .trace {{
    margin-top: 0.65rem;
  }}
  .trace > summary {{
    cursor: pointer;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.82rem;
    color: var(--muted);
  }}
  .trace-bar {{
    display: flex;
    height: 8px;
    border-radius: 999px;
    overflow: hidden;
    background: #e8eee9;
    margin: 0.5rem 0 0.35rem;
  }}
  .tb {{ display: block; height: 100%; }}
  .tb.context {{ background: var(--accent); }}
  .tb.planning {{ background: #8a97a3; }}
  .tb.other {{ background: #b5a89a; }}
  .trace-legend {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.75rem;
    color: var(--muted);
    margin: 0 0 0.45rem;
  }}
  .trace-steps {{
    list-style: none;
    padding: 0;
    margin: 0.35rem 0 0;
  }}
  .trace-steps li {{
    display: grid;
    grid-template-columns: auto 1fr auto;
    column-gap: 0.4rem;
    align-items: baseline;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.78rem;
    padding: 0.16rem 0;
    border-bottom: 1px solid var(--line);
  }}
  .trace-steps .chip {{
    font-size: 0.6rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 0.08rem 0.38rem;
    margin: 0;
  }}
  .trace-steps .pv {{
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }}
  .trace-steps .tok {{
    font-variant-numeric: tabular-nums;
    color: var(--muted);
    text-align: right;
  }}
  .ts-head {{
    color: var(--muted);
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    border-bottom: 1px solid var(--line);
  }}
  .ts-head .chip {{
    background: none;
    border: 0;
    padding-left: 0;
    color: inherit;
  }}
  .ts-head .pv,
  .ts-head .tok {{
    color: inherit;
    overflow: visible;
    text-overflow: unset;
  }}
  .ts-user .chip {{ background: #eef1f0; }}
  .ts-context .chip {{ background: var(--accent-soft); color: var(--accent); border-color: #b7d8c7; }}
  .ts-planning .chip {{ background: #eef1f4; }}
  .ts-other .chip {{ background: #f3efe9; }}
  .ts-code {{ opacity: 0.55; color: var(--muted); }}
  .ts-omit .pv {{ font-style: italic; }}
  .meta, .query, .muted, .footnote {{
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.85rem;
    color: var(--muted);
  }}
  code {{ font-size: 0.84em; }}
  footer.cta {{
    margin-top: 2.5rem;
    padding: 1.25rem;
    background: var(--accent-soft);
    border: 1px solid #b7d8c7;
    border-radius: 8px;
  }}
  footer.cta h2 {{ border: 0; padding: 0; margin: 0 0 0.5rem; }}
  @media print {{
    body {{ background: #fff; }}
    .toolbar, .no-print {{ display: none !important; }}
    .wrap {{ max-width: none; padding: 0; }}
    .card, .stat {{ break-inside: avoid; }}
    a {{ color: inherit; text-decoration: none; }}
    details.trace > *:not(summary) {{ display: none !important; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <header class="hero">
      <p class="eyebrow">Dosu · Knowledge report</p>
      <h1>{esc(org)}</h1>
      <p class="lede">{esc(summary)}</p>
      <p class="meta-line">
        Repo <code>{esc(repo_s)}</code>
        · branch <code>{esc(branch_s)}</code>
        · generated {esc(generated)}
      </p>
      <div class="toolbar no-print">
        <button type="button" onclick="window.print()">Print / Save as PDF</button>
      </div>
    </header>

    {token_section}

    <section>
      <h2>{esc(notes_heading)} <span class="muted">({len(candidates)})</span></h2>
      {f'<p class="lede">{esc(notes_lede)}</p>' if notes_lede else ""}
      {"".join(candidate_rows) if candidate_rows else '<p class="muted">No write_knowledge payloads yet. Run the skill (or dry-run) so the agent extracts learnings into --candidates.</p>'}
    </section>

    <section>
      <h2>Heaviest sessions</h2>
      <p class="lede">Where learning cost was highest — prime targets for Dosu cache hits.</p>
      <table>
        <thead>
          <tr>
            <th>Host</th><th>Session</th><th>Learning tokens</th><th>Rediscovery tools</th><th>Query</th>
          </tr>
        </thead>
        <tbody>
          {heavy_rows or '<tr><td colspan="5" class="muted">No sessions</td></tr>'}
        </tbody>
      </table>
    </section>

    <footer class="cta">
      <h2>{esc(footer_title)}</h2>
      <p>{esc(footer_body)}</p>
      <p class="meta">
        Print tip: use <strong>Print / Save as PDF</strong> above (or ⌘P / Ctrl+P).
        Logs never leave the engineer’s machine in this local flow — only note text is written to Dosu.
      </p>
      <div class="toolbar no-print">
        <button type="button" onclick="window.print()">Print / Save as PDF</button>
      </div>
    </footer>
  </div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, default=None)
    parser.add_argument("--token-report", type=Path, default=None)
    parser.add_argument("--pending", type=Path, default=None)
    parser.add_argument("--org-name", type=str, default=None)
    parser.add_argument("--repo", type=str, default=None)
    parser.add_argument("--branch", type=str, default=None)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--digest-dir",
        type=Path,
        default=Path("/tmp"),
        help="directory of digest-<transcript_id>.json files (default /tmp)",
    )
    parser.add_argument("--open", action="store_true", help="open in default browser")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="label missing-status candidates as proposed (dry-run HTML; default path assumes written)",
    )
    args = parser.parse_args(argv)

    inventory = load_json(args.inventory)
    candidates = load_json(args.candidates) if args.candidates else None
    token_report = load_json(args.token_report) if args.token_report else None
    pending = load_pending(args.pending) if args.pending else []

    html_out = build_report(
        inventory=inventory,
        candidates_doc=candidates,
        token_report=token_report,
        pending=pending,
        org_name=args.org_name,
        repo=args.repo,
        branch=args.branch,
        dry_run=args.dry_run,
        digest_dir=args.digest_dir,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_out, encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr)
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
