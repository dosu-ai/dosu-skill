#!/usr/bin/env python3
"""Compare baseline agent-log tokens vs a knowledge-assisted counterfactual.

Reads an inventory from parse_agent_logs.py and an eval file built after
write_knowledge + read_knowledge, then reports per-transcript and aggregate
token deltas.

Eval file schema (JSON):
{
  "repo": "<literal git remote get-url origin for the mined project>",
  "branch": "<branch>",
  "substitutions": [
    {
      "transcript_id": "<uuid>",
      "notes": [
        {
          "title": "page_version UniqueViolation race",
          "query": "detailed read_knowledge query used for eval",
          "read_knowledge_response": "<paste full tool response text>",
          "replaced_baseline_tokens": 12000
        }
      ]
    }
  ]
}

`replaced_baseline_tokens` is the estimated tokens from rediscovery work in the
original log that read_knowledge would replace (exploration Read/Grep/Shell
stretches that produced the durable fact). If omitted, the script falls back to
the transcript's rediscovery-weighted share of estimated_tokens.

Usage:
  python3 compare_tokens.py --baseline /tmp/inventory.json --eval /tmp/eval.json
  python3 compare_tokens.py --estimate-file response.txt
  python3 compare_tokens.py --estimate-text '...'
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(round(len(text) / CHARS_PER_TOKEN))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_replaced_tokens(transcript: dict[str, Any]) -> int:
    """Fallback: fraction of session tokens attributed to rediscovery tools."""
    est = int(transcript.get("estimated_tokens") or 0)
    rediscovery = int(transcript.get("rediscovery_tool_calls") or 0)
    tool_counts = transcript.get("tool_counts") or {}
    total_tools = sum(int(v) for v in tool_counts.values()) or 1
    share = min(0.85, rediscovery / total_tools)
    # Sessions that already read knowledge have less rediscovery to replace.
    if int(transcript.get("knowledge_reads") or 0) > 0:
        share *= 0.5
    return int(round(est * share))


def compare(baseline: dict[str, Any], eval_doc: dict[str, Any]) -> dict[str, Any]:
    by_id = {t["transcript_id"]: t for t in baseline.get("transcripts") or []}
    rows: list[dict[str, Any]] = []
    totals = {
        "baseline_tokens": 0,
        "replaced_baseline_tokens": 0,
        "read_knowledge_tokens": 0,
        "counterfactual_tokens": 0,
        "tokens_saved": 0,
        "notes_written": 0,
        "transcripts": 0,
    }

    for sub in eval_doc.get("substitutions") or []:
        tid = sub["transcript_id"]
        transcript = by_id.get(tid)
        if not transcript:
            rows.append(
                {
                    "transcript_id": tid,
                    "error": "transcript_id not found in baseline inventory",
                }
            )
            continue

        # Prefer host-reported tokens when the inventory has them.
        baseline_tok = int(
            transcript.get("reported_tokens") or transcript.get("estimated_tokens") or 0
        )
        notes = sub.get("notes") or []
        read_tok = 0
        replaced = 0
        note_rows: list[dict[str, Any]] = []
        for note in notes:
            response = note.get("read_knowledge_response") or ""
            q = note.get("query") or ""
            # Count query + response as the knowledge-assisted cost for that fact.
            note_read = estimate_tokens(response) + estimate_tokens(q)
            if note.get("read_knowledge_tokens") is not None:
                note_read = int(note["read_knowledge_tokens"])
            note_replaced = note.get("replaced_baseline_tokens")
            if note_replaced is None:
                # Split default rediscovery share across notes if multiple.
                note_replaced = 0  # filled after loop if all missing
            note_rows.append(
                {
                    "title": note.get("title"),
                    "query_tokens": estimate_tokens(q),
                    "response_tokens": estimate_tokens(response),
                    "read_knowledge_tokens": note_read,
                    "replaced_baseline_tokens": note_replaced,
                }
            )
            read_tok += note_read
            if (
                note_replaced is not None
                and note.get("replaced_baseline_tokens") is not None
            ):
                replaced += int(note_replaced)

        if replaced == 0:
            replaced = default_replaced_tokens(transcript)
            if note_rows:
                each = replaced // len(note_rows)
                rem = replaced - each * len(note_rows)
                for i, nr in enumerate(note_rows):
                    if nr["replaced_baseline_tokens"] in (None, 0):
                        nr["replaced_baseline_tokens"] = each + (rem if i == 0 else 0)

        # Counterfactual session ≈ baseline − rediscovery + read_knowledge cost
        counterfactual = max(0, baseline_tok - replaced + read_tok)
        saved = baseline_tok - counterfactual
        row = {
            "transcript_id": tid,
            "user_query": (transcript.get("user_queries") or [""])[0][:120],
            "baseline_tokens": baseline_tok,
            "replaced_baseline_tokens": replaced,
            "read_knowledge_tokens": read_tok,
            "counterfactual_tokens": counterfactual,
            "tokens_saved": saved,
            "pct_saved": (
                round(100.0 * saved / baseline_tok, 1) if baseline_tok else 0.0
            ),
            "already_wrote_knowledge": transcript.get("already_wrote_knowledge"),
            "notes": note_rows,
        }
        rows.append(row)
        totals["baseline_tokens"] += baseline_tok
        totals["replaced_baseline_tokens"] += replaced
        totals["read_knowledge_tokens"] += read_tok
        totals["counterfactual_tokens"] += counterfactual
        totals["tokens_saved"] += saved
        totals["notes_written"] += len(notes)
        totals["transcripts"] += 1

    if totals["baseline_tokens"]:
        totals["pct_saved"] = round(
            100.0 * totals["tokens_saved"] / totals["baseline_tokens"], 1
        )
    else:
        totals["pct_saved"] = 0.0

    return {
        "repo": eval_doc.get("repo"),
        "branch": eval_doc.get("branch"),
        "chars_per_token": CHARS_PER_TOKEN,
        "method": (
            "counterfactual_tokens = baseline_tokens - replaced_baseline_tokens "
            "+ read_knowledge_tokens (query+response). "
            "This estimates a re-run that loads facts via read_knowledge instead "
            "of rediscovering them in-session."
        ),
        "totals": totals,
        "per_transcript": rows,
    }


def print_report(report: dict[str, Any]) -> None:
    t = report["totals"]
    print("## Token comparison (baseline vs knowledge-assisted counterfactual)")
    print()
    print(f"transcripts evaluated: {t['transcripts']}")
    print(f"notes:                 {t['notes_written']}")
    print(f"baseline tokens:       {t['baseline_tokens']:,}")
    print(f"rediscovery replaced:  {t['replaced_baseline_tokens']:,}")
    print(f"read_knowledge cost:   {t['read_knowledge_tokens']:,}")
    print(f"counterfactual tokens: {t['counterfactual_tokens']:,}")
    print(f"tokens saved:          {t['tokens_saved']:,}  ({t['pct_saved']}%)")
    print()
    print(
        f"{'ID':<38} {'Base':>8} {'Repl':>8} {'Read':>8} {'After':>8} {'Saved':>8} {'%':>6}"
    )
    print("-" * 100)
    for row in report["per_transcript"]:
        if row.get("error"):
            print(f"{row['transcript_id']:<38} ERROR: {row['error']}")
            continue
        print(
            f"{row['transcript_id']:<38} "
            f"{row['baseline_tokens']:>8} "
            f"{row['replaced_baseline_tokens']:>8} "
            f"{row['read_knowledge_tokens']:>8} "
            f"{row['counterfactual_tokens']:>8} "
            f"{row['tokens_saved']:>8} "
            f"{row['pct_saved']:>5}%"
        )
        for note in row.get("notes") or []:
            print(
                f"  - {note.get('title')}: "
                f"replaced={note.get('replaced_baseline_tokens')} "
                f"read={note.get('read_knowledge_tokens')}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--baseline", type=Path, help="inventory JSON from parse_agent_logs.py"
    )
    parser.add_argument("--eval", type=Path, help="eval JSON with substitutions")
    parser.add_argument("--out", type=Path, help="write report JSON")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--estimate-text", type=str, help="estimate tokens for a string and exit"
    )
    parser.add_argument(
        "--estimate-file", type=Path, help="estimate tokens for a file and exit"
    )
    args = parser.parse_args(argv)

    if args.estimate_text is not None:
        n = estimate_tokens(args.estimate_text)
        print(json.dumps({"chars": len(args.estimate_text), "estimated_tokens": n}))
        return 0
    if args.estimate_file:
        text = args.estimate_file.read_text(encoding="utf-8")
        print(
            json.dumps(
                {
                    "path": str(args.estimate_file),
                    "chars": len(text),
                    "estimated_tokens": estimate_tokens(text),
                }
            )
        )
        return 0

    if not args.baseline or not args.eval:
        parser.error("--baseline and --eval are required unless using --estimate-*")

    report = compare(load_json(args.baseline), load_json(args.eval))
    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
