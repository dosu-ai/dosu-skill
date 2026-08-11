#!/usr/bin/env python3
"""Local pending queue for knowledge notes when Dosu MCP write is unavailable.

Stores append-only JSONL under .dosu/pending-knowledge.jsonl (project cwd).
Customers (or the skill) can sync later by reading the queue and calling
write_knowledge once MCP is connected.

Usage:
  python3 pending_knowledge.py append --repo URL --branch main \\
      --title "…" --content "…" [--tags a,b]
  python3 pending_knowledge.py list [--path .dosu/pending-knowledge.jsonl]
  python3 pending_knowledge.py export-mcp [--path …]   # print write payloads as JSON
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_REL = Path(".dosu") / "pending-knowledge.jsonl"


def queue_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser()
    return (Path.cwd() / DEFAULT_REL).resolve()


def append_note(
    path: Path,
    *,
    repo: str,
    branch: str,
    title: str,
    content: str,
    tags: list[str] | None,
) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "created_at": datetime.now(tz=UTC).isoformat(),
        "repo": repo,
        "branch": branch,
        "title": title,
        "content": content,
        "tags": tags or ["from-agent-log", "pending-sync"],
        "synced": False,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def iter_notes(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help=f"queue file (default: {DEFAULT_REL})",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    ap = sub.add_parser("append", help="append a pending note")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--content", required=True)
    ap.add_argument("--tags", default="", help="comma-separated tags")

    sub.add_parser("list", help="list pending notes")
    sub.add_parser(
        "export-mcp",
        help="print unsynced notes as write_knowledge argument objects",
    )

    args = parser.parse_args(argv)
    path = queue_path(args.path)

    if args.cmd == "append":
        tags = [t.strip() for t in args.tags.split(",") if t.strip()]
        row = append_note(
            path,
            repo=args.repo,
            branch=args.branch,
            title=args.title,
            content=args.content,
            tags=tags or None,
        )
        print(json.dumps({"ok": True, "path": str(path), "note": row}, indent=2))
        return 0

    notes = iter_notes(path)
    if args.cmd == "list":
        pending = [n for n in notes if not n.get("synced")]
        print(
            json.dumps(
                {
                    "path": str(path),
                    "total": len(notes),
                    "pending": len(pending),
                    "notes": pending,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    if args.cmd == "export-mcp":
        payloads = [
            {
                "title": n["title"],
                "content": n["content"],
                "repo": n["repo"],
                "branch": n["branch"],
                "tags": n.get("tags"),
            }
            for n in notes
            if not n.get("synced")
        ]
        print(json.dumps(payloads, indent=2, ensure_ascii=False))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
