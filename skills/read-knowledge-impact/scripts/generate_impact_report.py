#!/usr/bin/env python3
"""HTML report for a read_knowledge trajectory audit.

Usage:
  python3 generate_impact_report.py --findings /tmp/rk-findings.json --out report.html --open
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import webbrowser
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

OUTCOMES = (
    "relevant",
    "off_topic",
    "empty",
    "rejected",
    "overflow",
    "error",
    "distracting",
)

# Legacy findings used unused = "on-topic but not uniquely cited".
# Those belong in the relevant bucket.
LEGACY_RELEVANT = frozenset({"relevant", "unused"})

DEFAULT_WHAT = {
    "relevant": "Returned information was relevant to the question and solution.",
    "off_topic": "A result came back, but it was not about this question or solution.",
    "empty": "No knowledge found.",
    "rejected": "The user declined the call at a permission prompt.",
    "overflow": "Result dumped to a sidecar file the agent never opened — token and latency cost.",
    "error": "Parameter, repo-URL, or server error that burned a call.",
    "distracting": "The result was misleading or got in the way of the real answer.",
}

LABELS = {
    "relevant": "relevant",
    "off_topic": "off-topic",
    "empty": "empty result",
    "rejected": "rejected by user",
    "overflow": "overflow, never read",
    "error": "addressing errors",
    "distracting": "distracting",
}

HIGHLIGHT = "relevant"
WASTE = frozenset({"overflow", "error", "distracting"})
NO_EFFECT = frozenset({"off_topic", "empty", "rejected"})

RESULT_UNAVAILABLE = "Result payload unavailable in this log source."

# Session-viewer chrome (see SKILL.md "Session viewer"). Self-contained:
# native <dialog>, no network dependency, Escape-to-close for free.
VIEWER_CSS = """
  .sv-open {
    background: #232323; border: 1px solid #333; color: #c8c8c8;
    border-radius: 6px; font-size: 0.78rem; padding: 0.3rem 0.65rem;
    cursor: pointer; font-family: inherit;
  }
  .sv-open:hover, .sv-open:focus-visible { border-color: var(--good); color: var(--good); }
  dialog.sv {
    width: min(760px, 92vw); max-height: 85vh; margin: auto;
    background: #191919; color: var(--ink);
    border: 1px solid #2e2e2e; border-radius: 10px; padding: 0;
  }
  dialog.sv::backdrop { background: rgba(0, 0, 0, 0.6); }
  .sv-head {
    display: flex; justify-content: space-between; align-items: center;
    gap: 1rem; padding: 0.8rem 1rem; border-bottom: 1px solid var(--line);
    position: sticky; top: 0; background: #191919;
  }
  .sv-close {
    background: none; border: 1px solid #333; color: #c8c8c8;
    border-radius: 6px; cursor: pointer; font-size: 0.85rem;
    padding: 0.15rem 0.55rem; line-height: 1.4;
  }
  .sv-close:hover, .sv-close:focus-visible { border-color: var(--waste); color: var(--waste); }
  .sv-body { padding: 1rem; overflow-y: auto; max-height: calc(85vh - 3.4rem); outline: none; }
  .sv-turn {
    border: 1px solid #262626; border-radius: 8px;
    padding: 0.6rem 0.8rem; margin: 0.55rem 0; font-size: 0.85rem;
  }
  .sv-turn.sv-pinned { border-color: #3a4a6b; background: #171a21; }
  .sv-turn.sv-hl { border-color: #245c42; background: #14211a; }
  .sv-turn.sv-thinking { opacity: 0.72; }
  .sv-role {
    font-size: 0.66rem; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--muted); margin-bottom: 0.3rem;
  }
  .sv-turn.sv-hl .sv-role { color: var(--good); }
  .sv-turn.sv-pinned .sv-role { color: #8ab0e8; }
  .sv-text { white-space: pre-wrap; overflow-wrap: anywhere; }
  .sv-action {
    color: var(--muted); font-size: 0.75rem; border-style: dashed;
    padding: 0.35rem 0.8rem;
  }
  .sv-io {
    margin: 0.3rem 0 0; padding: 0.4rem 0.55rem;
    background: #101010; border-radius: 5px;
    font-size: 0.72rem; line-height: 1.4;
    white-space: pre-wrap; overflow-wrap: anywhere;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: #b9b9b9; max-height: 140px; overflow: auto;
  }
  .sv-io-out { color: #8f8f8f; border-left: 2px solid #2e2e2e; }
  .sv-gap { text-align: center; color: var(--muted); font-size: 0.75rem; margin: 0.4rem 0; }
  .sv-block { margin: 0.5rem 0; border: 1px solid var(--line); border-radius: 6px; overflow: hidden; }
  .sv-block-head {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.3rem 0.6rem; background: #202020; font-size: 0.68rem;
    text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted);
  }
  .sv-block pre {
    margin: 0; padding: 0.6rem; max-height: 320px; overflow: auto;
    font-size: 0.78rem; line-height: 1.45; white-space: pre-wrap;
    overflow-wrap: anywhere;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  }
  .sv-copy {
    background: none; border: 1px solid #333; color: #c8c8c8;
    border-radius: 4px; font-size: 0.68rem; padding: 0.1rem 0.45rem;
    cursor: pointer; font-family: inherit; text-transform: none; letter-spacing: 0;
  }
  .sv-copy:hover, .sv-copy:focus-visible { border-color: var(--good); color: var(--good); }
  .sv-note { color: var(--waste); font-size: 0.78rem; padding: 0.4rem 0.1rem; }
  .card-actions { margin-top: 0.7rem; }
"""

VIEWER_JS = """<script>
(function () {
  function fallbackCopy(pre) {
    var range = document.createRange();
    range.selectNodeContents(pre);
    var sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
    try { document.execCommand('copy'); } catch (err) {}
    sel.removeAllRanges();
  }
  document.addEventListener('click', function (e) {
    var opener = e.target.closest('.sv-open');
    if (opener) {
      var dlg = document.getElementById(opener.getAttribute('data-sv'));
      if (dlg) {
        dlg.showModal();
        var body = dlg.querySelector('.sv-body');
        if (body) body.focus();
      }
      return;
    }
    var copy = e.target.closest('.sv-copy');
    if (copy) {
      var pre = copy.closest('.sv-block').querySelector('pre');
      var text = pre ? pre.innerText : '';
      var done = function () {
        copy.textContent = 'Copied';
        setTimeout(function () { copy.textContent = 'Copy'; }, 1200);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () { fallbackCopy(pre); done(); });
      } else { fallbackCopy(pre); done(); }
      return;
    }
    var dlg2 = e.target.closest('dialog.sv');
    if (dlg2 && e.target === dlg2) dlg2.close();
  });
  // Escape closes the viewer even where the native <dialog> cancel is
  // suppressed (some embedded browser panes).
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = document.querySelector('dialog.sv[open]');
    if (open) { open.close(); e.preventDefault(); }
  });
})();
</script>"""


def _sv_turn_html(turn: dict[str, Any]) -> str:
    kind = turn.get("kind")
    if kind == "gap":
        return "<div class='sv-gap'>⋯ turns omitted ⋯</div>"
    if kind == "action":
        name = esc(turn.get("text"))
        inp = turn.get("input")
        out = turn.get("output")
        if not inp and not out:
            return f"<div class='sv-turn sv-action'>→ {name}</div>"
        bits = [f"<div class='sv-turn sv-action'><div class='sv-role'>→ {name}</div>"]
        if inp:
            bits.append(f"<pre class='sv-io'>{esc(inp)}</pre>")
        if out:
            bits.append(
                f"<pre class='sv-io sv-io-out'>{esc(out)}</pre>"
            )
        bits.append("</div>")
        return "".join(bits)
    if kind == "read_knowledge":
        hl = bool(turn.get("highlighted"))
        cls = "sv-turn sv-hl" if hl else "sv-turn"
        role = "read_knowledge — this call" if hl else "read_knowledge"
        parts = [f"<div class='{cls}'>", f"<div class='sv-role'>{esc(role)}</div>"]
        parts.append(
            "<div class='sv-block'><div class='sv-block-head'><span>Query</span>"
            "<button class='sv-copy' type='button'>Copy</button></div>"
            f"<pre>{esc(turn.get('query') or '—')}</pre></div>"
        )
        result = turn.get("result")
        if turn.get("result_available") and result:
            note = (
                "<div class='sv-note'>Long result truncated for display.</div>"
                if turn.get("result_truncated")
                else ""
            )
            parts.append(
                "<div class='sv-block'><div class='sv-block-head'>"
                "<span>Knowledge returned</span>"
                "<button class='sv-copy' type='button'>Copy</button></div>"
                f"<pre>{esc(result)}</pre></div>{note}"
            )
        elif hl:
            parts.append(f"<div class='sv-note'>{esc(RESULT_UNAVAILABLE)}</div>")
        parts.append("</div>")
        return "".join(parts)
    role = turn.get("role") or ""
    pinned = bool(turn.get("pinned"))
    cls = f"sv-turn sv-{esc(role)}"
    label = {"user": "User", "assistant": "Agent"}.get(role, role or "…")
    if kind == "thinking":
        cls += " sv-thinking"
        label = "Agent · reasoning"
    if pinned:
        cls += " sv-pinned"
        label = "Task"
    return (
        f"<div class='{cls}'><div class='sv-role'>{esc(label)}</div>"
        f"<div class='sv-text'>{esc(turn.get('text'))}</div></div>"
    )


def session_view_html(call: dict[str, Any], fallback_id: str) -> tuple[str, str]:
    """(Review-session button, <dialog>) for one call — ('', '') without a view."""
    view = call.get("session_view") or {}
    turns = view.get("turns") or []
    if not turns:
        return "", ""
    dialog_id = "sv-" + re.sub(r"[^A-Za-z0-9_-]+", "-", str(call.get("id") or fallback_id))
    button = (
        "<div class='card-actions'>"
        f"<button class='sv-open' type='button' data-sv='{dialog_id}'>Review session</button>"
        "<noscript><span class='sv-note'>Viewer needs JavaScript — open this"
        " report in a browser.</span></noscript>"
        "</div>"
    )
    # Identify source and session only — never raw filesystem paths.
    ident = f"{esc(call.get('source') or '?')} · {esc(call.get('transcript_id') or 'session')}"
    body = "".join(_sv_turn_html(t) for t in turns)
    dialog = (
        f"<dialog class='sv' id='{dialog_id}' aria-label='Session transcript'>"
        "<div class='sv-head'><div><strong>Session review</strong> "
        f"<span class='muted'>{ident}</span></div>"
        "<form method='dialog'><button class='sv-close' aria-label='Close'>✕</button></form>"
        f"</div><div class='sv-body' tabindex='0'>{body}</div></dialog>"
    )
    return button, dialog


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def load_findings(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return {"calls": raw}
    calls = raw.get("calls")
    if not isinstance(calls, list):
        raise SystemExit("findings JSON must be {\"calls\": [...]} or a list")
    return raw


def pct(part: int, whole: int) -> int:
    if whole <= 0:
        return 0
    return round(100 * part / whole)


def _raw_outcome(call: dict[str, Any]) -> str:
    raw = call.get("outcome") or call.get("hint") or "off_topic"
    if raw == "unused":
        return "relevant"
    if raw == "unknown":
        return "off_topic"
    return raw


def summarize(calls: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(_raw_outcome(c) for c in calls)
    sessions = {c.get("transcript_id") for c in calls if c.get("transcript_id")}
    workspaces = {c.get("cwd") for c in calls if c.get("cwd")}
    sources = Counter(c.get("source") or "?" for c in calls)
    n = len(calls)
    relevant = counts["relevant"]
    waste = sum(counts[k] for k in WASTE)
    no_effect = sum(counts[k] for k in NO_EFFECT)
    return {
        "counts": {k: counts[k] for k in OUTCOMES},
        "n": n,
        "sessions": len(sessions),
        "workspaces": len(workspaces),
        "sources": dict(sources),
        "relevant": relevant,
        "waste": waste,
        "no_effect": no_effect,
        "relevant_pct": pct(relevant, n),
        "waste_pct": pct(waste, n),
    }


def stacked_bar(relevant: int, no_effect: int, waste: int) -> str:
    total = max(relevant + no_effect + waste, 1)
    return f"""
<div class="bar" role="img" aria-label="Outcome mix">
  <span class="seg relevant" style="width:{100 * relevant / total:.2f}%"></span>
  <span class="seg none" style="width:{100 * no_effect / total:.2f}%"></span>
  <span class="seg waste" style="width:{100 * waste / total:.2f}%"></span>
</div>
<div class="bar-legend">
  <div><strong>{relevant}</strong> relevant<br><span class="muted">on-topic for the question and solution</span></div>
  <div><strong>{no_effect}</strong> no effect<br><span class="muted">off-topic, empty, or declined</span></div>
  <div><strong>{waste}</strong> waste<br><span class="muted">distracting, overflow, or errors</span></div>
</div>
"""


FOLD_CHARS = 220
KNOWLEDGE_PREFIXES = ("Team knowledge on: ", "Knowledge: ", "Dosu returned: ")


def _looks_cut_off(text: str) -> bool:
    s = text.rstrip()
    if not s:
        return False
    return s[-1].isalnum()


def _strip_knowledge_prefix(text: str) -> str:
    for prefix in KNOWLEDGE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return text.strip()


def _longest(*parts: Any) -> str:
    candidates = [str(p).strip() for p in parts if p]
    return max(candidates, key=len) if candidates else "—"


def card_task(call: dict[str, Any]) -> str:
    return _longest(call.get("task"), call.get("user_task"))


def card_knowledge(call: dict[str, Any]) -> str:
    knowledge = str(call.get("knowledge") or "").strip()
    query = str(call.get("query") or "").strip()
    preview = str(call.get("result_preview") or "").strip()
    if knowledge and query:
        stripped = _strip_knowledge_prefix(knowledge)
        if query.startswith(stripped) or stripped.startswith(query[:40]):
            if len(query) > len(stripped) or _looks_cut_off(knowledge):
                knowledge = query
    return knowledge or query or preview


def card_impact(call: dict[str, Any]) -> str:
    return _longest(call.get("impact"))


def fold_dd(label: str, text: str) -> str:
    body = (text or "").strip() or "—"
    if len(body) <= FOLD_CHARS:
        return f"<dt>{esc(label)}</dt><dd>{esc(body)}</dd>"
    cut = body[:FOLD_CHARS].rsplit(" ", 1)[0]
    if len(cut) < FOLD_CHARS // 3:
        cut = body[:FOLD_CHARS]
    rest = body[len(cut) :].lstrip()
    return (
        f"<dt>{esc(label)}</dt><dd>"
        f"<details class='fold'>"
        f"<summary>{esc(cut)}… <span class='more'>more</span></summary>"
        f"<div class='full'>{esc(body)}</div>"
        f"</details></dd>"
    )


def cards_for(calls: list[dict[str, Any]], outcomes: set[str], heading: str) -> str:
    rows = [c for c in calls if (c.get("outcome") or c.get("hint")) in outcomes]
    if not rows:
        return ""
    articles = []
    for i, c in enumerate(rows):
        outcome = _raw_outcome(c)
        tone = "good" if outcome in LEGACY_RELEVANT or outcome == "relevant" else "bad"
        review_btn, review_dialog = session_view_html(c, f"{heading}-{i}")
        articles.append(
            f"""
<article class="card {tone}">
  <div class="badge {tone}">{esc(LABELS.get(outcome, outcome))}</div>
  <dl>
    {fold_dd("task", card_task(c))}
    {fold_dd("knowledge", card_knowledge(c))}
    {fold_dd("impact", card_impact(c))}
  </dl>
  {review_btn}
  <p class="meta">{esc(c.get("source"))} · {esc(c.get("transcript_id") or "")}</p>
  {review_dialog}
</article>
"""
        )
    return f"<h2>{esc(heading)}</h2>" + "".join(articles)


def _parse_iso_date(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            parsed = datetime.strptime(raw[:19] if "T" in raw and fmt.startswith("%Y-%m-%dT%H:%M:%S") and "%z" not in fmt else raw[:10] if fmt == "%Y-%m-%d" else raw, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except ValueError:
        return None


def resolve_window(doc: dict[str, Any], *, days_override: int | None = None) -> dict[str, Any]:
    window = dict(doc.get("window") or {})
    if days_override is not None:
        window["days"] = days_override
    calls = list(doc.get("calls") or [])
    stamps: list[datetime] = []
    for call in calls:
        parsed = _parse_iso_date(call.get("called_at")) or _parse_iso_date(call.get("mtime"))
        if parsed is not None:
            stamps.append(parsed)
    end = _parse_iso_date(window.get("end")) or (
        max(stamps) if stamps else datetime.now(tz=UTC)
    )
    days = window.get("days")
    if not isinstance(days, int) or days <= 0:
        start_existing = _parse_iso_date(window.get("start"))
        if start_existing is not None:
            days = max(1, (end.date() - start_existing.date()).days or 1)
        elif stamps:
            days = max(1, (end.date() - min(stamps).date()).days or 1)
        else:
            days = 30
        window["days"] = days
    start = _parse_iso_date(window.get("start")) or (end - timedelta(days=days))
    window["start"] = start.date().isoformat()
    window["end"] = end.date().isoformat()
    window["days"] = days
    return window


def window_labels(days: int) -> tuple[str, str, str]:
    """Return (stat label, lede span, eyebrow is unused)."""
    if days <= 1:
        return "1 day", "the last day", "1 day"
    if days == 7:
        return "7 days", "the last week", "7 days"
    return f"{days} days", f"the last {days} days", f"{days} days"


def build_report(doc: dict[str, Any], *, days_override: int | None = None) -> str:
    calls = list(doc.get("calls") or [])
    # Session-viewer contract: every highlight must open a working viewer.
    missing = [
        c
        for c in calls
        if _raw_outcome(c) == "relevant"
        and not ((c.get("session_view") or {}).get("turns"))
    ]
    if missing:
        ids = ", ".join(str(c.get("id") or "?") for c in missing[:5])
        raise SystemExit(
            f"{len(missing)} highlight call(s) missing session_view.turns "
            f"(e.g. {ids}). Re-run extract_read_knowledge.py and carry "
            "session_view into the findings — every highlight needs a "
            "working Review session viewer."
        )
    stats = summarize(calls)
    window = resolve_window(doc, days_override=days_override)
    start = window["start"]
    end = window["end"]
    days = window["days"]
    days_label, span, _ = window_labels(int(days))
    notes = doc.get("outcome_notes") or {}
    lede = doc.get("lede") or (
        f"Every call the tool made across {span} of agent sessions, traced "
        "through the transcript: was the information relevant to the question "
        "and solution — or did it get in the way?"
    )
    generated = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    source_bits = ", ".join(
        f"{k} {v}" for k, v in sorted(stats["sources"].items())
    ) or "—"

    table_rows = []
    for key in OUTCOMES:
        n = stats["counts"][key]
        if n == 0:
            continue
        tone = "good" if key == "relevant" else ("bad" if key in WASTE else "muted")
        what = DEFAULT_WHAT[key] if key == "relevant" else (notes.get(key) or DEFAULT_WHAT[key])
        table_rows.append(
            f"<tr><td><span class='badge {tone}'>{esc(LABELS[key])}</span></td>"
            f"<td class='num'>{n}</td><td>{esc(what)}</td></tr>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>read_knowledge Impact Audit</title>
<style>
  :root {{
    --bg: #141414;
    --card: #1c1c1c;
    --ink: #ececec;
    --muted: #8a8a8a;
    --line: #2a2a2a;
    --good: #3dd68c;
    --good-bg: #163526;
    --waste: #e07a5f;
    --waste-bg: #3a221c;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--ink);
    font-family: ui-sans-serif, system-ui, -apple-system, sans-serif;
    line-height: 1.45;
  }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 2.5rem 1.5rem 4rem; }}
  .eyebrow {{
    text-transform: uppercase;
    letter-spacing: 0.12em;
    font-size: 0.7rem;
    color: var(--muted);
    margin: 0 0 0.75rem;
  }}
  h1 {{ font-size: 2rem; font-weight: 600; margin: 0 0 0.6rem; letter-spacing: -0.03em; }}
  h1 code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }}
  h2 {{ font-size: 1.05rem; margin: 2.2rem 0 0.8rem; font-weight: 600; }}
  .lede {{ color: var(--muted); margin: 0 0 1.5rem; max-width: 46rem; }}
  .stats {{
    display: grid;
    grid-template-columns: repeat(4, minmax(0, 1fr));
    gap: 0.75rem;
    margin: 1.25rem 0 1.5rem;
  }}
  @media (max-width: 720px) {{ .stats {{ grid-template-columns: 1fr 1fr; }} }}
  .stat {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.9rem 1rem;
  }}
  .stat .value {{ font-size: 1.7rem; font-weight: 600; letter-spacing: -0.03em; }}
  .stat .value.good {{ color: var(--good); }}
  .stat .value.waste {{ color: var(--waste); }}
  .stat .label {{ color: var(--muted); font-size: 0.8rem; margin-top: 0.2rem; }}
  .bar {{
    display: flex;
    height: 14px;
    border-radius: 99px;
    overflow: hidden;
    background: #2a2a2a;
    margin: 0.5rem 0 0.9rem;
  }}
  .seg {{ display: block; height: 100%; }}
  .seg.relevant {{ background: var(--good); }}
  .seg.none {{ background: #5c5c5c; }}
  .seg.waste {{ background: var(--waste); }}
  .bar-legend {{
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 1rem;
    font-size: 0.9rem;
    margin-bottom: 1.5rem;
  }}
  .muted {{ color: var(--muted); font-size: 0.8rem; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.86rem; }}
  th, td {{ text-align: left; padding: 0.7rem 0.45rem; border-bottom: 1px solid var(--line); vertical-align: top; }}
  th {{ color: var(--muted); font-size: 0.72rem; letter-spacing: 0.08em; text-transform: uppercase; font-weight: 600; }}
  td.num {{ text-align: right; font-variant-numeric: tabular-nums; width: 4rem; }}
  .badge {{
    display: inline-block;
    font-size: 0.72rem;
    padding: 0.15rem 0.45rem;
    border-radius: 4px;
    background: #2a2a2a;
    color: #c8c8c8;
  }}
  .badge.good {{ background: var(--good-bg); color: var(--good); }}
  .badge.bad {{ background: var(--waste-bg); color: var(--waste); }}
  .card {{
    background: var(--card);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 1rem 1.1rem;
    margin: 0.7rem 0;
  }}
  .card.good {{ border-color: #245c42; }}
  .card.bad {{ border-color: #5a332a; }}
  dl {{ margin: 0.6rem 0 0; }}
  dt {{
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin-top: 0.55rem;
  }}
  dd {{ margin: 0.15rem 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }}
  details.fold > summary {{
    cursor: pointer;
    list-style: none;
  }}
  details.fold > summary::-webkit-details-marker {{ display: none; }}
  details.fold > summary .more {{
    color: var(--good);
    font-size: 0.78rem;
    margin-left: 0.25rem;
  }}
  details.fold[open] > summary {{ display: none; }}
  details.fold .full {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
  .meta {{ color: var(--muted); font-size: 0.75rem; margin: 0.7rem 0 0; }}
  .foot {{ color: var(--muted); font-size: 0.75rem; margin-top: 2.5rem; }}
{VIEWER_CSS}
</style>
</head>
<body>
<div class="wrap">
  <p class="eyebrow">Dosu MCP · trajectory audit · {esc(start)} – {esc(end)}</p>
  <h1><code>read_knowledge</code> Impact Audit</h1>
  <p class="lede">{esc(lede)}</p>
  <div class="stats">
    <div class="stat"><div class="value">{stats["n"]}</div><div class="label">calls in {esc(days_label)}</div></div>
    <div class="stat"><div class="value">{stats["sessions"]}</div><div class="label">sessions · {stats["workspaces"]} workspaces</div></div>
    <div class="stat"><div class="value good">{stats["relevant_pct"]}%</div><div class="label">relevant to the question and solution</div></div>
    <div class="stat"><div class="value waste">{stats["waste_pct"]}%</div><div class="label">distracting, overflow, or errors</div></div>
  </div>
  {stacked_bar(stats["relevant"], stats["no_effect"], stats["waste"])}
  <h2>Outcomes</h2>
  <table>
    <thead><tr><th>Outcome</th><th>Calls</th><th>What happened</th></tr></thead>
    <tbody>
      {''.join(table_rows) or '<tr><td colspan="3" class="muted">No classified calls.</td></tr>'}
    </tbody>
  </table>
  {cards_for(calls, {HIGHLIGHT}, "Highlights")}
  {cards_for(calls, set(WASTE), "Failures")}
  <p class="foot">Generated {esc(generated)} · {esc(source_bits)} · classified from local agent transcripts</p>
</div>
{VIEWER_JS}
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--findings", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--open", action="store_true")
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="report window in days (overrides findings.window.days)",
    )
    args = parser.parse_args(argv)

    html_out = build_report(load_findings(args.findings), days_override=args.days)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(html_out, encoding="utf-8")
    print(f"Wrote {args.out}", file=sys.stderr)
    if args.open:
        webbrowser.open(args.out.resolve().as_uri())
    return 0


def _self_test() -> None:
    now = datetime.now(tz=UTC)
    sample_view = {
        "result_available": True,
        "turns": [
            {"role": "user", "kind": "message", "text": "fix the flaky test", "pinned": True},
            {
                "role": "assistant",
                "kind": "read_knowledge",
                "query": "flaky test retry conventions",
                "result": "Retries are configured in conftest.py.",
                "result_available": True,
                "highlighted": True,
            },
            {
                "role": "assistant",
                "kind": "action",
                "text": "Bash",
                "input": "pytest tests/flaky -x",
                "output": "1 passed in 0.42s",
            },
            {"role": "assistant", "kind": "message", "text": "Using the conftest retry hook."},
        ],
    }
    missing_window = {
        "calls": [
            {
                "mtime": now.isoformat(),
                "outcome": "relevant",
                "source": "cursor",
                "session_view": sample_view,
            },
            {
                "mtime": (now - timedelta(hours=5)).isoformat(),
                "outcome": "empty",
                "source": "cursor",
            },
        ]
    }
    window = resolve_window(missing_window)
    assert window["days"] == 1, window
    html_out = build_report(missing_window)
    assert "calls in 1 day" in html_out
    assert "across the last day of agent sessions" in html_out
    assert "30 days" not in html_out
    assert "a month" not in html_out

    # Session viewer: button + dialog + pinned/highlighted turns + copy chrome.
    assert "Review session" in html_out
    assert "dialog class='sv'" in html_out
    assert "flaky test retry conventions" in html_out
    assert "Retries are configured in conftest.py." in html_out
    assert "sv-pinned" in html_out and "sv-hl" in html_out
    assert html_out.count("sv-copy") >= 2
    assert RESULT_UNAVAILABLE not in html_out
    # Action turns render their sanitized input and output previews.
    assert "pytest tests/flaky -x" in html_out
    assert "1 passed in 0.42s" in html_out

    # Unrecoverable result → honest unavailable state, never the preview.
    no_result = {
        "calls": [
            {
                "outcome": "relevant",
                "source": "cursor",
                "result_preview": "preview text that is NOT complete",
                "session_view": {
                    "result_available": False,
                    "turns": [
                        {
                            "role": "assistant",
                            "kind": "read_knowledge",
                            "query": "q",
                            "result": None,
                            "result_available": False,
                            "highlighted": True,
                        }
                    ],
                },
            }
        ]
    }
    assert RESULT_UNAVAILABLE in build_report(no_result)

    # A highlight without a session_view must fail the build.
    try:
        build_report({"calls": [{"outcome": "relevant", "source": "cursor"}]})
    except SystemExit as err:
        assert "session_view" in str(err)
    else:
        raise AssertionError("highlight without session_view must fail")
    pinned = build_report({"window": {"days": 1, "start": "2026-08-20", "end": "2026-08-21"}, "calls": []})
    assert "calls in 1 day" in pinned
    month = build_report({"window": {"days": 30, "start": "2026-07-22", "end": "2026-08-21"}, "calls": []})
    assert "calls in 30 days" in month
    assert "last 30 days" in month

    preview = '{"result":"<search_results>\\n<source title=\\"Hotspots\\">"}' + (" x" * 200)
    preview = preview[:400]
    summary = "Production Performance Hotspots: update_page timeouts kill the nested agent."
    query = "Why does a trivial agent tool call take two seconds?"
    call = {
        "knowledge": summary,
        "query": query,
        "result_preview": preview,
    }
    assert card_knowledge(call) == summary
    assert card_knowledge({**call, "knowledge": summary.rstrip(".")}) == summary.rstrip(".")
    assert card_knowledge({**call, "knowledge": ""}) == query
    assert card_knowledge({"result_preview": preview}) == preview
    assert (
        card_knowledge(
            {
                "knowledge": "Team knowledge on: Why does a trivial",
                "query": query,
                "result_preview": preview,
            }
        )
        == query
    )


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
        print("ok")
        raise SystemExit(0)
    raise SystemExit(main())
