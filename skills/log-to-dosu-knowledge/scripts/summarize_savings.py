#!/usr/bin/env python3
"""Print the default user-facing savings summary for mined notes.

Analytics model (get_knowledge_token_savings):
  tokens saved on a hit = that page's generation_tokens

For log-mined branch notes, approx_rediscovery_tokens is the generation-cost
proxy (tokens spent rediscovering the fact in the source session). Expected
savings for one future agent read of this set ≈ sum(approx_rediscovery_tokens).

Usage:
  python3 summarize_savings.py --candidates /tmp/dosu-log-candidates.json
  python3 summarize_savings.py --candidates /tmp/dosu-log-candidates.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_candidates(path: Path) -> list[dict[str, Any]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, list):
        return doc
    return list(doc.get("candidates") or [])


def summarize(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    titles: list[str] = []
    per_note: list[dict[str, Any]] = []
    total = 0
    missing = 0
    for c in candidates:
        title = str(c.get("title") or "").strip() or "(untitled)"
        titles.append(title)
        raw = c.get("approx_rediscovery_tokens")
        if raw is None:
            missing += 1
            tok = 0
        else:
            tok = max(0, int(raw))
        total += tok
        per_note.append({"title": title, "approx_rediscovery_tokens": tok})
    return {
        "notes_cached": len(candidates),
        "expected_savings_tokens": total,
        "notes_missing_rediscovery_estimate": missing,
        "notes": per_note,
        "model": (
            "analytics-style: expected savings per future agent read ≈ "
            "Σ approx_rediscovery_tokens (generation/rediscovery cost reused on each hit)"
        ),
    }


def format_text(summary: dict[str, Any]) -> str:
    n = summary["notes_cached"]
    lines = [f"Cached {n} note{'s' if n != 1 else ''}:"]
    if n == 0:
        lines.append("(none)")
    else:
        for i, note in enumerate(summary["notes"], 1):
            lines.append(f"{i}. {note['title']}")
    lines.append("")
    y = summary["expected_savings_tokens"]
    lines.append(f"Expected savings: ~{y:,} tokens per future agent read")
    lines.append(
        "(same model as analytics: rediscovery/generation cost reused on each hit)"
    )
    if summary["notes_missing_rediscovery_estimate"]:
        lines.append(
            f"(warning: {summary['notes_missing_rediscovery_estimate']} note(s) "
            "missing approx_rediscovery_tokens — total may be undercounted)"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = summarize(load_candidates(args.candidates))
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        sys.stdout.write(format_text(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
