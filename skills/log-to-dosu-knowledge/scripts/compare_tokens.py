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

`replaced_baseline_tokens` / `approx_rediscovery_tokens` is the cost to learn
THIS fact: tokens spent arriving at it (question + retrieval + thinking + the
conclusion). That is Decant context + planning + other on the selected
investigation-stretch lines. Write/Edit and mutating shell do not count — a
note cannot save implementation tokens. If that stretch cost 100k to learn,
the note is 100k and a future read saves 100k. Do not cap. Do not use a
session share.

If omitted, the estimate stays 0 — compare() must not invent a session-sized
number. Do not restore session estimated_tokens × min(0.85,
rediscovery_tool_calls / total_tools), and do not split that share equally
across notes from the same transcript. That formula attributes the unit of
the session (including unrelated work) via a call-count ratio, so a 356k chat
could claim ~295k for one tangent. Context-bucket only is also wrong: it
drops the assistant conclusion, so Slack OAuth could show 124 from an
unrelated Grep.

Measure the stretch instead:

  python3 compare_tokens.py --from-digest /tmp/digest-<id>.json --lines 40-88
  python3 compare_tokens.py --from-transcript path.jsonl --lines 40-88,102
  python3 compare_tokens.py --self-test

Usage:
  python3 compare_tokens.py --baseline /tmp/inventory.json --eval /tmp/eval.json
  python3 compare_tokens.py --estimate-file response.txt
  python3 compare_tokens.py --estimate-text '...'
  python3 compare_tokens.py --from-digest /tmp/digest-<id>.json --lines 40-88
  python3 compare_tokens.py --from-transcript path.jsonl --lines 40-88,102
  python3 compare_tokens.py --self-test
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from activity_buckets import (
    learning_tokens_for_jsonl_lines as _learning_tokens_for_jsonl_lines,
)

CHARS_PER_TOKEN = 4.0


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return int(round(len(text) / CHARS_PER_TOKEN))


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def default_replaced_tokens(transcript: dict[str, Any]) -> int:
    """Return 0 when a note omits replaced_baseline_tokens.

    Do not restore the old session-share formula:

        estimated_tokens * min(0.85, rediscovery_tool_calls / total_tools)
        then split equally across notes from the same transcript

    That was wrong: the unit is the fact's investigation stretch, not the
    session; call-count share is not token mass; equal split assumes every
    note caused all rediscovery. A 350k session with 85/100 rediscovery
    tools would have claimed 297500 for whatever note used the fallback.
    REDISCOVERY_TOOLS in parse_agent_logs.py is for ranking only — not
    token attribution. Transcript is accepted for call-site compatibility
    and ignored.
    """
    del transcript
    return 0


def parse_line_spec(spec: str) -> set[int]:
    """Parse '40-88,102,110-115' into 1-based JSONL line numbers."""
    out: set[int] = set()
    if not spec or not str(spec).strip():
        return out
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, end_s = part.split("-", 1)
            start, end = int(start_s.strip()), int(end_s.strip())
            if end < start:
                start, end = end, start
            out.update(range(start, end + 1))
        else:
            out.add(int(part))
    return out


def tokens_for_jsonl_lines(path: Path, lines: set[int]) -> int:
    """Cost to learn THIS fact: context + planning + other chars/4.

    Includes the question, retrieval, thinking, and the conclusion.
    Write/Edit/mutating shell contribute 0. If the selected stretch cost
    100k to learn, this returns 100k. No cap. No session share.
    """
    return _learning_tokens_for_jsonl_lines(path, lines)


def transcript_learning_tokens(transcript: dict[str, Any]) -> int:
    """Savings baseline: cost to learn (non-code). Never estimated/effective.

    Prefer transcript.learning_tokens. Fall back to context+planning+other
    when those fields are present. Never use context_tokens alone.
    """
    if "learning_tokens" in transcript:
        return int(transcript.get("learning_tokens") or 0)
    planning = transcript.get("planning_tokens")
    other = transcript.get("other_tokens")
    if planning is not None or other is not None:
        return (
            int(transcript.get("context_tokens") or 0)
            + int(planning or 0)
            + int(other or 0)
        )
    return 0


def sum_digest_turn_tokens(digest: dict[str, Any], lines: set[int] | None) -> int:
    """Sum digest turn est_tokens whose `line` is in `lines`. Returns 0 if no lines.

    Prefer tokens_for_jsonl_lines on the raw transcript: digest previews are truncated.
    """
    if not lines:
        return 0
    total = 0
    for turn in digest.get("turns") or []:
        if turn.get("line") in lines:
            total += int(turn.get("est_tokens") or 0)
    return total


def investigation_tokens_from_digest(digest: dict[str, Any], lines: set[int]) -> dict[str, Any]:
    """If digest.summary.path exists as a file, use tokens_for_jsonl_lines.

    Else fall back to sum_digest_turn_tokens.
    Return {approx_rediscovery_tokens, source, lines, path}.
    """
    path_str = (digest.get("summary") or {}).get("path")
    path = Path(path_str) if path_str else None
    if path is not None and path.is_file():
        return {
            "approx_rediscovery_tokens": tokens_for_jsonl_lines(path, lines),
            "source": "jsonl_lines",
            "lines": sorted(lines),
            "path": str(path),
        }
    return {
        "approx_rediscovery_tokens": sum_digest_turn_tokens(digest, lines),
        "source": "digest_turns",
        "lines": sorted(lines),
        "path": path_str,
    }


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

        # Savings baseline is cost to learn — never estimated/effective,
        # never context_tokens alone.
        baseline_tok = transcript_learning_tokens(transcript)
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
            if note.get("replaced_baseline_tokens") is not None:
                replaced += int(note_replaced)
                note_replaced_out = int(note_replaced)
            else:
                note_replaced_out = 0
            note_rows.append(
                {
                    "title": note.get("title"),
                    "query_tokens": estimate_tokens(q),
                    "response_tokens": estimate_tokens(response),
                    "read_knowledge_tokens": note_read,
                    "replaced_baseline_tokens": note_replaced_out,
                }
            )
            read_tok += note_read

        if replaced == 0:
            # Omitted estimates stay 0. Do not split a session budget.
            replaced = default_replaced_tokens(transcript)

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
            "of rediscovering them in-session. replaced_baseline_tokens is the "
            "learning stretch of each fact (omit → 0; never session × "
            "call-share). baseline_tokens is transcript.learning_tokens "
            "(fallback context+planning+other; never context alone)."
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


def run_self_test() -> int:
    # 1. Old session-share formula must not be restored.
    old_claimed = int(round(350000 * min(0.85, 85 / 100)))
    assert old_claimed == 297500, old_claimed
    big_session = {
        "transcript_id": "t-big",
        "estimated_tokens": 350000,
        "rediscovery_tool_calls": 85,
        "tool_counts": {"Grep": 85, "Edit": 15},
        "user_queries": ["investigate slack rate limits"],
    }
    assert default_replaced_tokens(big_session) == 0

    # 4. Line spec parser.
    assert parse_line_spec("40-42,50") == {40, 41, 42, 50}

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        write_only = root / "write.jsonl"
        write_only.write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"path": "a.py", "contents": "x" * 1000},
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert tokens_for_jsonl_lines(write_only, {1}) == 0

        user_dump = root / "user.jsonl"
        user_dump.write_text(
            json.dumps(
                {
                    "role": "user",
                    "message": {"content": [{"type": "text", "text": "x" * 10000}]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert tokens_for_jsonl_lines(user_dump, {1}) == 2500

        assistant_conclusion = root / "asst.jsonl"
        conclusion = "The Slack OAuth redirect must use the loopback callback."
        assistant_conclusion.write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [{"type": "text", "text": conclusion}]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert tokens_for_jsonl_lines(assistant_conclusion, {1}) == int(
            round(len(conclusion) / CHARS_PER_TOKEN)
        )

        result_body = "x" * 400000
        huge = root / "huge.jsonl"
        huge.write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "grep-1",
                                "name": "Grep",
                                "input": {"pattern": "UniqueViolation"},
                            }
                        ]
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "grep-1",
                                "content": result_body,
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert tokens_for_jsonl_lines(huge, {2}) == 100000

        pytest_shell = root / "pytest.jsonl"
        pytest_shell.write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Shell",
                                "input": {"command": "pytest"},
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert tokens_for_jsonl_lines(pytest_shell, {1}) == 0

        rg_shell = root / "rg.jsonl"
        rg_shell.write_text(
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Shell",
                                "input": {"command": "rg UniqueViolation"},
                            }
                        ]
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert tokens_for_jsonl_lines(rg_shell, {1}) > 0

        # Context-tool lines: middle result is not the whole-file total.
        p = root / "three.jsonl"
        line1 = (
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "r1",
                                "name": "Read",
                                "input": {"path": "a.py"},
                            }
                        ]
                    },
                }
            )
            + "\n"
        )
        mid_content = "MIDDLE_LINE_ONLY_XXXX"
        line2 = (
            json.dumps(
                {
                    "role": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "r1",
                                "content": mid_content,
                            }
                        ]
                    },
                }
            )
            + "\n"
        )
        line3 = (
            json.dumps(
                {
                    "role": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "r2",
                                "name": "Read",
                                "input": {"path": "b.py"},
                            }
                        ]
                    },
                }
            )
            + "\n"
        )
        p.write_text(line1 + line2 + line3, encoding="utf-8")
        mid = tokens_for_jsonl_lines(p, {2})
        assert mid == int(round(len(mid_content) / CHARS_PER_TOKEN)), (mid, len(mid_content))
        whole = tokens_for_jsonl_lines(p, {1, 2, 3})
        assert mid != whole
        assert mid < whole

        # Prefer raw JSONL when digest.summary.path exists.
        digest_with_path = {
            "summary": {"path": str(p)},
            "turns": [
                {"line": 1, "est_tokens": 1},
                {"line": 2, "est_tokens": 1},
                {"line": 3, "est_tokens": 1},
            ],
        }
        measured = investigation_tokens_from_digest(digest_with_path, {2})
        assert measured["source"] == "jsonl_lines"
        assert measured["approx_rediscovery_tokens"] == mid
        assert measured["lines"] == [2]
        assert measured["path"] == str(p)

        missing_path = {
            "summary": {"path": str(root / "no-such.jsonl")},
            "turns": [
                {"line": 10, "est_tokens": 100},
                {"line": 11, "est_tokens": 250},
                {"line": 12, "est_tokens": 50},
            ],
        }
        fallback = investigation_tokens_from_digest(missing_path, {11, 12})
        assert fallback["source"] == "digest_turns"
        assert fallback["approx_rediscovery_tokens"] == 300

    # 5. Digest turn sum: no lines → 0; selected turns only.
    digest = {
        "turns": [
            {"line": 10, "est_tokens": 100},
            {"line": 11, "est_tokens": 250},
            {"line": 12, "est_tokens": 50},
        ]
    }
    assert sum_digest_turn_tokens(digest, set()) == 0
    assert sum_digest_turn_tokens(digest, {11, 12}) == 300

    # 6. compare() must not invent a session-sized replaced total.
    report = compare(
        {"transcripts": [big_session]},
        {
            "substitutions": [
                {
                    "transcript_id": "t-big",
                    "notes": [
                        {
                            "title": "local Slack OAuth tangent",
                            "query": "slack oauth",
                            "read_knowledge_response": "short",
                        }
                    ],
                }
            ]
        },
    )
    assert report["totals"]["replaced_baseline_tokens"] == 0
    assert report["per_transcript"][0]["replaced_baseline_tokens"] == 0
    assert report["per_transcript"][0]["notes"][0]["replaced_baseline_tokens"] == 0
    # Explicit stretch still counts (and is not capped).
    explicit = compare(
        {"transcripts": [big_session]},
        {
            "substitutions": [
                {
                    "transcript_id": "t-big",
                    "notes": [
                        {
                            "title": "real stretch",
                            "query": "q",
                            "read_knowledge_response": "r",
                            "replaced_baseline_tokens": 100000,
                        }
                    ],
                }
            ]
        },
    )
    assert explicit["totals"]["replaced_baseline_tokens"] == 100000

    # Baseline is learning_tokens (or context+planning+other), never context alone.
    assert transcript_learning_tokens({"context_tokens": 50000}) == 0
    assert transcript_learning_tokens(
        {"learning_tokens": 80000, "context_tokens": 100}
    ) == 80000
    assert (
        transcript_learning_tokens(
            {"context_tokens": 10, "planning_tokens": 20, "other_tokens": 30}
        )
        == 60
    )
    ctx_only = {**big_session, "context_tokens": 50000}
    ctx_report = compare(
        {"transcripts": [ctx_only]},
        {
            "substitutions": [
                {
                    "transcript_id": "t-big",
                    "notes": [
                        {
                            "title": "x",
                            "query": "q",
                            "read_knowledge_response": "r",
                        }
                    ],
                }
            ]
        },
    )
    assert ctx_report["per_transcript"][0]["baseline_tokens"] == 0
    learned = {**big_session, "learning_tokens": 12000}
    learned_report = compare(
        {"transcripts": [learned]},
        {
            "substitutions": [
                {
                    "transcript_id": "t-big",
                    "notes": [
                        {
                            "title": "x",
                            "query": "q",
                            "read_knowledge_response": "r",
                        }
                    ],
                }
            ]
        },
    )
    assert learned_report["per_transcript"][0]["baseline_tokens"] == 12000

    print("self-test OK")
    return 0


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
    parser.add_argument(
        "--from-digest",
        type=Path,
        help="digest JSON from parse_agent_logs.py --digest --json",
    )
    parser.add_argument(
        "--from-transcript",
        type=Path,
        help="raw agent JSONL; chars/4 of --lines is the stretch",
    )
    parser.add_argument(
        "--lines",
        type=str,
        help="1-based JSONL line spec, e.g. 40-88,102,110-115",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

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

    if args.from_digest is not None:
        if args.lines is None:
            parser.error("--from-digest requires --lines")
        result = investigation_tokens_from_digest(
            load_json(args.from_digest), parse_line_spec(args.lines)
        )
        print(json.dumps(result))
        return 0

    if args.from_transcript is not None:
        if args.lines is None:
            parser.error("--from-transcript requires --lines")
        lines = parse_line_spec(args.lines)
        result = {
            "approx_rediscovery_tokens": tokens_for_jsonl_lines(
                args.from_transcript, lines
            ),
            "source": "jsonl_lines",
            "lines": sorted(lines),
            "path": str(args.from_transcript),
        }
        print(json.dumps(result))
        return 0

    if not args.baseline or not args.eval:
        parser.error(
            "--baseline and --eval are required unless using "
            "--estimate-* / --from-digest / --from-transcript / --self-test"
        )

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

