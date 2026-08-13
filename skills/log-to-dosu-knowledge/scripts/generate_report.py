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
      "why_durable": "took 40 rediscovery tool calls to relearn",
      "approx_rediscovery_tokens": 12000,
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
import sys
import webbrowser
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


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
    """
    if not candidates:
        return None
    replaced = 0
    has_rediscovery = False
    read_cost = 0
    ids: set[str] = set()
    for c in candidates:
        tid = c.get("transcript_id")
        if tid:
            ids.add(str(tid))
        raw = c.get("approx_rediscovery_tokens")
        if raw is not None:
            has_rediscovery = True
            replaced += max(0, int(raw))
        blob = f"{c.get('title') or ''}\n{c.get('content') or ''}"
        read_cost += int(round(len(blob) / CHARS_PER_TOKEN))
    if not has_rediscovery:
        return None
    baseline = 0
    for t in inventory.get("transcripts") or []:
        if str(t.get("transcript_id") or "") in ids:
            baseline += effective_tokens(t)
    if not baseline:
        baseline = int((inventory.get("totals") or {}).get("effective_tokens") or 0)
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
                "why_durable": "queued locally (MCP write unavailable)",
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

    Existing written/pending/proposed values are kept (case-insensitive).
    Always returns copies.
    """
    fallback = "proposed" if dry_run else "written"
    known = {"written", "pending", "proposed"}
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


def notes_section_copy(candidates: list[dict[str, Any]]) -> tuple[str, str, str, str]:
    """Heading, lede, footer title, footer body — based on candidate statuses."""
    n = len(candidates)
    written = sum(1 for c in candidates if (c.get("status") or "written").lower() == "written")
    pending = sum(1 for c in candidates if (c.get("status") or "").lower() == "pending")
    proposed = n - written - pending
    if n > 0 and written == n:
        return (
            "Notes written to Dosu",
            "These notes were written with write_knowledge — title + content, not the original user prompts.",
            "Share",
            "Notes are on the backfill branch and in the candidate-topic pipeline. Print / Save as PDF if you want a copy.",
        )
    if n > 0 and proposed == n:
        return (
            "Proposed write_knowledge calls",
            "Exact notes that would be written — title + content, not the original user prompts.",
            "Next step",
            "Run the skill (without dry-run) so these payloads are written via write_knowledge.",
        )
    if n == 0:
        return (
            "write_knowledge notes",
            "Exact notes — title + content, not the original user prompts.",
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
    return (
        "write_knowledge notes",
        f"{', '.join(bits)} — title + content, not the original user prompts.",
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
) -> str:
    totals = inventory.get("totals") or {}
    by_source = totals.get("by_source") or {}
    transcripts = inventory.get("transcripts") or []

    cand_doc = candidates_doc or {}
    candidates = list(cand_doc.get("candidates") or [])
    # Never invent "notes" from inventory user prompts — those are not
    # write_knowledge payloads. Empty list until the agent extracts learnings.
    candidates = merge_pending(candidates, pending)
    candidates = apply_status_defaults(candidates, dry_run=dry_run)

    org = org_name or cand_doc.get("org_name") or "Your team"
    repo_s = repo or cand_doc.get("repo") or inventory.get("cwd") or "—"
    branch_s = branch or cand_doc.get("branch") or "—"
    summary = cand_doc.get("summary") or (
        "Local agent session logs were mined for durable engineering knowledge "
        "that Dosu can reuse on the next task — reducing rediscovery cost."
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

    # Heavy sessions (top by tokens)
    heavy = sorted(transcripts, key=effective_tokens, reverse=True)[:8]

    source_chips = "".join(
        f'<span class="chip">{esc(k)}: {esc(v)}</span>' for k, v in by_source.items()
    )
    notes_heading, notes_lede, footer_title, footer_body = notes_section_copy(candidates)

    candidate_rows = []
    for i, c in enumerate(candidates, 1):
        body = c.get("content") or ""
        body_html = (
            f'<pre class="note-body">{esc(body)}</pre>'
            if body
            else "<p class='muted'>Session-level gap — extract a lean note before writing to Dosu.</p>"
        )
        status = c.get("status") or "written"
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
  {f'<p class="query"><strong>Trigger:</strong> {esc(c.get("user_query"))}</p>' if c.get("user_query") else ""}
  <p><strong>Why durable:</strong> {esc(c.get("why_durable") or "—")}</p>
  {body_html}
</article>
""")

    heavy_rows = "".join(f"""<tr>
      <td>{esc(t.get("source"))}</td>
      <td><code>{esc(t.get("transcript_id"))}</code></td>
      <td class="num">{fmt_int(effective_tokens(t))}</td>
      <td class="num">{fmt_int(t.get("rediscovery_tool_calls"))}</td>
      <td class="num">{fmt_int(t.get("knowledge_writes"))}</td>
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
    <div class="stat"><div class="label">Baseline tokens</div><div class="value">{fmt_int(tok.get("baseline_tokens"))}</div></div>
    <div class="stat"><div class="label">Rediscovery replaced</div><div class="value">{fmt_int(tok.get("replaced_baseline_tokens"))}</div></div>
    <div class="stat"><div class="label">Read cost</div><div class="value">{fmt_int(tok.get("read_knowledge_tokens"))}</div></div>
    <div class="stat highlight"><div class="label">Est. tokens saved</div><div class="value">{fmt_int(tok.get("tokens_saved"))} <span class="pct">({esc(pct_s)})</span></div></div>
  </div>
  <p class="footnote">{esc(
        "From each note's approx_rediscovery_tokens minus estimated note-read cost (chars/4). Same model as the chat savings line. Not a billing invoice."
        if derived_savings
        else "Relative estimate (host-reported tokens when available, else chars/4). Not a billing invoice. Large unrelated branch-note dumps in read_knowledge can erase savings — lean notes matter."
    )}</p>
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
  button.secondary, .btn.secondary {{
    background: transparent;
    color: var(--ink);
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
  .status-proposed, .status-session-gap {{ background: #eef1f0; }}
  .note-body {{
    white-space: pre-wrap;
    font-family: ui-sans-serif, system-ui, sans-serif;
    font-size: 0.86rem;
    background: var(--bg);
    border-radius: 4px;
    padding: 0.75rem;
    overflow-x: auto;
  }}
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
        <button type="button" class="secondary" onclick="navigator.clipboard.writeText(location.href)">Copy page URL</button>
      </div>
    </header>

    <section>
      <h2>Session inventory</h2>
      <div class="stats">
        <div class="stat"><div class="label">Sessions</div><div class="value">{fmt_int(totals.get("transcripts"))}</div></div>
        <div class="stat"><div class="label">Effective tokens</div><div class="value">{fmt_int(totals.get("effective_tokens"))}</div></div>
        <div class="stat"><div class="label">Write gaps</div><div class="value">{fmt_int(totals.get("write_gaps"))}</div></div>
        <div class="stat"><div class="label">Already wrote knowledge</div><div class="value">{fmt_int(totals.get("with_write_knowledge"))}</div></div>
      </div>
      <p>{source_chips or '<span class="muted">No per-host breakdown</span>'}</p>
    </section>

    {token_section}

    <section>
      <h2>{esc(notes_heading)} <span class="muted">({len(candidates)})</span></h2>
      <p class="lede">{esc(notes_lede)}</p>
      {"".join(candidate_rows) if candidate_rows else '<p class="muted">No write_knowledge payloads yet. Run the skill (or dry-run) so the agent extracts learnings into --candidates.</p>'}
    </section>

    <section>
      <h2>Heaviest sessions</h2>
      <p class="lede">Where context pressure was highest — prime targets for Dosu cache hits.</p>
      <table>
        <thead>
          <tr>
            <th>Host</th><th>Session</th><th>Tokens</th><th>Rediscovery tools</th><th>Writes</th><th>Query</th>
          </tr>
        </thead>
        <tbody>
          {heavy_rows or '<tr><td colspan="6" class="muted">No sessions</td></tr>'}
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
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_out, encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr)
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
