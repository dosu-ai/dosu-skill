#!/usr/bin/env python3
"""Extract Dosu read_knowledge MCP calls from Cursor / Claude / Codex logs.

Usage:
  python3 extract_read_knowledge.py --days 30 --all-projects --out /tmp/rk-calls.json
  python3 extract_read_knowledge.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

READ_TOOLS = frozenset({"read_knowledge", "read_org_knowledge", "init_knowledge"})
OVERFLOW_MARKERS = (
    "large output has been written to",
    "output has been written to",
    "output_file",
    "agent-tools/",
)
EMPTY_MARKERS = (
    "no knowledge found",
    "no knowledge found.",
)
REJECT_MARKERS = (
    "user rejected",
    "user declined",
    "permission denied",
    "rejected by user",
)
ERROR_MARKERS = (
    "invalid repo",
    "unknown tool",
    "validation error",
    "expected string",
    "failed to",
    '"iserror": true',
    '"is_error": true',
)
USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE
)
PREVIEW_CHARS = 400

CURSOR_TS_RE = re.compile(
    r"(?P<month>Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+"
    r"(?P<day>\d{1,2}),\s+(?P<year>\d{4}),\s+"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s+(?P<ampm>AM|PM)"
    r"(?:\s*\(UTC(?P<off>[+-]\d{1,2})\))?",
    re.I,
)
_MONTHS = {
    m: i
    for i, m in enumerate(
        "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1
    )
}
TS_TAG_RE = re.compile(r"<timestamp>\s*(.*?)\s*</timestamp>", re.I | re.S)


def _parse_iso_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _parse_cursor_ts(text: str) -> datetime | None:
    m = CURSOR_TS_RE.search(text or "")
    if not m:
        return None
    hour = int(m["hour"]) % 12
    if m["ampm"].upper() == "PM":
        hour += 12
    off = int(m["off"] or "-7")
    from datetime import timezone

    tz = timezone(timedelta(hours=off))
    return datetime(
        int(m["year"]),
        _MONTHS[m["month"][:3].title()],
        int(m["day"]),
        hour,
        int(m["minute"]),
        tzinfo=tz,
    ).astimezone(UTC)


def _record_timestamp(obj: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "createdAt", "created_at"):
        parsed = _parse_iso_dt(obj.get(key))
        if parsed is not None:
            return parsed
    blobs: list[str] = []
    msg = obj.get("message")
    if isinstance(msg, dict):
        content = msg.get("content")
        if isinstance(content, str):
            blobs.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and isinstance(block.get("text"), str):
                    blobs.append(block["text"])
    if isinstance(obj.get("content"), str):
        blobs.append(obj["content"])
    for blob in blobs:
        for raw in TS_TAG_RE.findall(blob):
            parsed = _parse_cursor_ts(raw) or _parse_iso_dt(raw)
            if parsed is not None:
                return parsed
        parsed = _parse_cursor_ts(blob)
        if parsed is not None:
            return parsed
    return None


def _call_when(row: dict[str, Any]) -> datetime | None:
    return _parse_iso_dt(row.get("called_at")) or _parse_iso_dt(row.get("mtime"))




def _parser_scripts_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    candidates = [
        here.parents[1] / "log-to-dosu-knowledge" / "scripts",
        here.parents[2] / "log-to-dosu-knowledge" / "scripts",
        here,
    ]
    for path in candidates:
        if (path / "parse_agent_logs.py").is_file():
            return path
    return None


def _load_discover():
    scripts = _parser_scripts_dir()
    if scripts is None:
        return None
    sys.path.insert(0, str(scripts))
    import parse_agent_logs as pal  # type: ignore

    return pal


def _knowledge_call(name: str | None, inp: Any) -> dict[str, Any] | None:
    if not name:
        return None
    if name in READ_TOOLS:
        return {"tool": name, "arguments": inp if isinstance(inp, dict) else {}}
    if name in {"CallMcpTool", "mcp__dosu__read_knowledge"} or (
        name.startswith("mcp__") and name.rsplit("__", 1)[-1] in READ_TOOLS
    ):
        if name.startswith("mcp__"):
            tool = name.rsplit("__", 1)[-1]
            if tool in READ_TOOLS:
                return {"tool": tool, "arguments": inp if isinstance(inp, dict) else {}}
        if isinstance(inp, dict):
            tool_name = inp.get("toolName") or inp.get("tool_name") or inp.get("name")
            if tool_name in READ_TOOLS:
                args = inp.get("arguments") or inp.get("input") or {}
                return {
                    "tool": tool_name,
                    "server": inp.get("server"),
                    "arguments": args if isinstance(args, dict) else {},
                }
    if name.startswith("mcp__") and "__" in name[5:]:
        tool = name.rsplit("__", 1)[-1]
        if tool in READ_TOOLS:
            return {"tool": tool, "arguments": inp if isinstance(inp, dict) else {}}
    for kt in READ_TOOLS:
        if kt in name:
            args = inp
            if isinstance(inp, str):
                try:
                    args = json.loads(inp)
                except json.JSONDecodeError:
                    args = {"raw": inp}
            return {"tool": kt, "arguments": args if isinstance(args, dict) else {}}
    return None


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)
    except TypeError:
        return str(content)


def _hint(result: str, *, is_error: bool) -> str:
    low = (result or "").lower()
    if is_error:
        return "error"
    if any(m in low for m in REJECT_MARKERS):
        return "rejected"
    if any(m in low for m in OVERFLOW_MARKERS) and len(result) < 8000:
        return "overflow"
    if len(result) > 80_000:
        return "overflow"
    if any(m in low for m in EMPTY_MARKERS) and len(result) < 4000:
        return "empty"
    if any(m in low for m in ERROR_MARKERS) and "search_results" not in low:
        return "error"
    return "unknown"


def _task_from_queries(queries: list[str]) -> str:
    for q in queries:
        text = (q or "").strip()
        if not text:
            continue
        if text.startswith("# AGENTS.md") or text.startswith("<INSTRUCTIONS>"):
            continue
        one = re.sub(r"\s+", " ", text)[:180]
        return one
    return ""


def _args_query(args: dict[str, Any]) -> str:
    q = args.get("query") or args.get("task") or ""
    return str(q)[:500] if q else ""



def _walk_extract(obj: Any, *, open_call, close_call, queries: list[str]) -> None:
    if isinstance(obj, list):
        for item in obj:
            _walk_extract(
                item, open_call=open_call, close_call=close_call, queries=queries
            )
        return
    if not isinstance(obj, dict):
        return
    name = obj.get("name") or obj.get("function_name") or obj.get("toolName")
    inp = obj.get("input") or obj.get("arguments") or obj.get("params")
    if isinstance(name, str):
        kcall = _knowledge_call(name, inp)
        if kcall:
            open_call(
                kcall, obj.get("id") or obj.get("call_id") or obj.get("tool_use_id")
            )
    btype = obj.get("type")
    payload = obj.get("payload")
    if isinstance(payload, dict) and not btype:
        btype = payload.get("type")
    if btype in {"tool_result", "function_call_output", "function_call_result"}:
        src = payload if isinstance(payload, dict) else obj
        close_call(
            src.get("call_id") or src.get("tool_use_id") or src.get("id") or obj.get("id"),
            _result_text(
                src.get("output")
                or src.get("content")
                or obj.get("output")
                or obj.get("content")
                or obj.get("result")
            ),
            is_error=bool(obj.get("is_error") or obj.get("isError")),
        )
    role = obj.get("role") or obj.get("speaker") or obj.get("type")
    text_val = obj.get("text")
    if isinstance(obj.get("content"), str) and not text_val:
        text_val = obj.get("content")
    if isinstance(text_val, str) and role in {"user", "human", "user_message"}:
        queries.extend(USER_QUERY_RE.findall(text_val) or [text_val[:2000]])
    for value in obj.values():
        if isinstance(value, (dict, list)):
            _walk_extract(
                value, open_call=open_call, close_call=close_call, queries=queries
            )


def _extract_generic(path: Path, session_id: str) -> dict[str, Any]:
    scripts = _parser_scripts_dir()
    if scripts is not None and str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from generic_logs import load_records  # type: ignore

    pending: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    queries: list[str] = []
    seq = 0
    last_ts: datetime | None = None

    def _open_call(kcall: dict[str, Any], tool_id: str | None) -> None:
        nonlocal seq
        args = kcall.get("arguments") if isinstance(kcall.get("arguments"), dict) else {}
        row = {
            "source": "generic",
            "transcript_id": session_id,
            "path": str(path),
            "tool": kcall.get("tool"),
            "server": kcall.get("server"),
            "query": _args_query(args),
            "repo": args.get("repo"),
            "branch": args.get("branch"),
            "note_id": args.get("note_id"),
        }
        if last_ts is not None:
            row["called_at"] = last_ts.isoformat()
        key = str(tool_id) if tool_id else f"generic:{session_id}:{seq}"
        if not tool_id:
            seq += 1
        pending[key] = row

    def _close_call(tool_id: str | None, text: str, *, is_error: bool = False) -> None:
        nonlocal seq
        row = None
        if tool_id and str(tool_id) in pending:
            row = pending.pop(str(tool_id))
        elif len(pending) == 1:
            _, row = pending.popitem()
        if row is None:
            return
        row["id"] = f"generic:{session_id}:{seq}"
        seq += 1
        row["result_chars"] = len(text)
        row["result_preview"] = text[:PREVIEW_CHARS]
        row["hint"] = _hint(text, is_error=is_error)
        calls.append(row)

    for rec in load_records(path):
        if isinstance(rec, dict):
            ts = _record_timestamp(rec)
            if ts is not None:
                last_ts = ts
        _walk_extract(
            rec, open_call=_open_call, close_call=_close_call, queries=queries
        )
    for item in pending.values():
        item["id"] = f"generic:{session_id}:{seq}"
        seq += 1
        item["hint"] = "unknown"
        item["result_preview"] = ""
        item["result_chars"] = 0
        calls.append(item)
    task = _task_from_queries(queries)
    for row in calls:
        row["user_task"] = task
        row["mtime"] = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    return {"calls": calls, "user_task": task}


def extract_from_jsonl(path: Path, source: str, session_id: str) -> dict[str, Any]:
    if source == "generic":
        return _extract_generic(path, session_id)
    pending: dict[str, dict[str, Any]] = {}
    calls: list[dict[str, Any]] = []
    queries: list[str] = []
    cwd: str | None = None
    seq = 0

    def _flush_unmatched() -> None:
        nonlocal seq
        for item in pending.values():
            item["id"] = f"{source}:{session_id}:{seq}"
            seq += 1
            item["hint"] = "unknown"
            item["result_preview"] = ""
            item["result_chars"] = 0
            calls.append(item)
        pending.clear()

    last_ts: datetime | None = None

    def _open_call(kcall: dict[str, Any], tool_id: str | None) -> None:
        nonlocal seq
        args = kcall.get("arguments") if isinstance(kcall.get("arguments"), dict) else {}
        row = {
            "source": source,
            "transcript_id": session_id,
            "path": str(path),
            "tool": kcall.get("tool"),
            "server": kcall.get("server"),
            "query": _args_query(args),
            "repo": args.get("repo"),
            "branch": args.get("branch"),
            "note_id": args.get("note_id"),
        }
        if last_ts is not None:
            row["called_at"] = last_ts.isoformat()
        if tool_id:
            pending[str(tool_id)] = row
        else:
            row["id"] = f"{source}:{session_id}:{seq}"
            seq += 1
            pending[row["id"]] = row

    def _close_call(tool_id: str | None, text: str, *, is_error: bool = False) -> None:
        nonlocal seq
        row = None
        if tool_id and str(tool_id) in pending:
            row = pending.pop(str(tool_id))
        elif len(pending) == 1:
            _, row = pending.popitem()
        if row is None:
            return
        row["id"] = f"{source}:{session_id}:{seq}"
        seq += 1
        row["result_chars"] = len(text)
        row["result_preview"] = text[:PREVIEW_CHARS]
        row["hint"] = _hint(text, is_error=is_error)
        m = re.search(
            r"(?:written to|output_file)[:\s]+([^\s]+\.(?:txt|json|jsonl))",
            text,
            re.I,
        )
        if m:
            row["overflow_path"] = m.group(1)
        calls.append(row)

    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                ts = _record_timestamp(obj)
                if ts is not None:
                    last_ts = ts

            if source == "codex":
                etype = obj.get("type")
                payload = obj.get("payload") or {}
                if etype == "session_meta" and not cwd:
                    cwd = payload.get("cwd")
                if etype == "event_msg" and payload.get("type") == "user_message":
                    msg = payload.get("message") or payload.get("text") or ""
                    if isinstance(msg, str) and msg.strip():
                        queries.extend(USER_QUERY_RE.findall(msg) or [msg[:2000]])
                if etype == "response_item" and payload.get("type") == "function_call":
                    name = payload.get("name")
                    args = payload.get("arguments")
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"raw": args}
                    kcall = _knowledge_call(name, args)
                    if kcall:
                        _open_call(kcall, payload.get("call_id") or payload.get("id"))
                if etype == "response_item" and payload.get("type") in {
                    "function_call_output",
                    "function_call_result",
                }:
                    _close_call(
                        payload.get("call_id") or payload.get("id"),
                        _result_text(payload.get("output") or payload.get("content")),
                    )
                continue

            role = obj.get("role")
            etype = obj.get("type")
            if obj.get("cwd") and not cwd:
                cwd = obj.get("cwd")
            msg = obj.get("message") or {}
            content = msg.get("content") if isinstance(msg, dict) else None
            if content is None:
                content = obj.get("content")
            blocks: list[Any]
            if isinstance(content, list):
                blocks = content
            elif isinstance(content, str):
                blocks = [{"type": "text", "text": content}]
            else:
                blocks = []

            texts: list[str] = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in {"text", "input_text", "output_text"}:
                    texts.append(block.get("text") or "")
                elif btype == "tool_use":
                    kcall = _knowledge_call(block.get("name"), block.get("input"))
                    if kcall:
                        _open_call(
                            kcall,
                            block.get("id") or block.get("tool_use_id"),
                        )
                elif btype == "tool_result":
                    chunk = _result_text(block.get("content"))
                    _close_call(
                        block.get("tool_use_id")
                        or block.get("toolUseId")
                        or block.get("id"),
                        chunk,
                        is_error=bool(block.get("is_error") or block.get("isError")),
                    )

            is_user = role == "user" or etype == "user"
            if is_user and texts:
                joined = "\n".join(texts)
                if not (
                    blocks
                    and all(
                        isinstance(b, dict) and b.get("type") == "tool_result"
                        for b in blocks
                    )
                ):
                    queries.extend(USER_QUERY_RE.findall(joined) or [joined[:2000]])

    _flush_unmatched()
    task = _task_from_queries(queries)
    for row in calls:
        row["cwd"] = cwd
        row["user_task"] = task
        row["mtime"] = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()
    return {"calls": calls, "cwd": cwd, "user_task": task}


def run_self_test() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cursor = root / "cursor.jsonl"
        cursor.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "role": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "<timestamp>Tuesday, Aug 4, 2026, 3:28 PM (UTC-7)</timestamp>\n<user_query>fix rbac mypy</user_query>",
                                    }
                                ]
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "role": "assistant",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "rk1",
                                        "name": "CallMcpTool",
                                        "input": {
                                            "server": "user-dosu",
                                            "toolName": "read_knowledge",
                                            "arguments": {
                                                "query": "Confluence RBAC mypy typing"
                                            },
                                        },
                                    }
                                ]
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "role": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "rk1",
                                        "content": "JSON payloads must be dict[str, Any]",
                                    }
                                ]
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "role": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": "<timestamp>Friday, Aug 21, 2026, 10:00 AM (UTC-7)</timestamp>\n<user_query>try again</user_query>",
                                    }
                                ]
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "role": "assistant",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "id": "rk2",
                                        "name": "mcp__dosu__read_knowledge",
                                        "input": {"query": "missing page"},
                                    }
                                ]
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "role": "user",
                            "message": {
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "rk2",
                                        "content": "No knowledge found. Continue with the task.",
                                    }
                                ]
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        out = extract_from_jsonl(cursor, "cursor", "sess-1")
        assert len(out["calls"]) == 2, out
        assert out["calls"][0]["query"] == "Confluence RBAC mypy typing"
        assert out["calls"][0]["hint"] == "unknown"
        assert out["calls"][1]["hint"] == "empty"
        assert "fix rbac mypy" in out["user_task"]
        assert out["calls"][0].get("called_at", "").startswith("2026-08-04")
        assert out["calls"][1].get("called_at", "").startswith("2026-08-21")
        cutoff = datetime(2026, 8, 20, tzinfo=UTC)
        recent = [c for c in out["calls"] if (_call_when(c) or cutoff) >= cutoff]
        assert len(recent) == 1, recent
        assert recent[0]["query"] == "missing page"

        claude = root / "claude.jsonl"
        claude.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "cwd": "/tmp/app",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "c1",
                                "name": "mcp__user-dosu__read_knowledge",
                                "input": {"query": "auth tokens", "repo": "x"},
                            }
                        ],
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "c1",
                                "is_error": True,
                                "content": "Invalid repo",
                            }
                        ],
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cl = extract_from_jsonl(claude, "claude", "sess-2")
        assert cl["calls"][0]["hint"] == "error"
        assert cl["cwd"] == "/tmp/app"

        codex = root / "codex.jsonl"
        codex.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "call_id": "f1",
                        "name": "read_knowledge",
                        "arguments": json.dumps({"query": "overflow test"}),
                    },
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "f1",
                        "output": "Large output has been written to /tmp/agent-tools/out.txt",
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
        cx = extract_from_jsonl(codex, "codex", "sess-3")
        assert cx["calls"][0]["hint"] == "overflow", cx["calls"][0]
        mystery = root / "mystery.jsonl"
        mystery.write_text(
            json.dumps({"speaker": "user", "text": "fix oauth retry"})
            + "\n"
            + json.dumps(
                {
                    "name": "read_knowledge",
                    "id": "g1",
                    "arguments": {"query": "oauth retry loop"},
                }
            )
            + "\n"
            + json.dumps(
                {
                    "type": "tool_result",
                    "id": "g1",
                    "content": "No knowledge found.",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        gen = extract_from_jsonl(mystery, "generic", "sess-g")
        assert len(gen["calls"]) == 1, gen
        assert gen["calls"][0]["query"] == "oauth retry loop"
        assert gen["calls"][0]["hint"] == "empty"

        print("self-test OK")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--sources", default="cursor,claude,codex,generic")
    parser.add_argument("--dir", type=Path, default=None)
    parser.add_argument("--cwd", type=Path, default=None)
    parser.add_argument("--all-projects", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--include-subagents", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    pal = _load_discover()
    if pal is None:
        print(
            "parse_agent_logs.py not found (install log-to-dosu-knowledge beside this skill)",
            file=sys.stderr,
        )
        return 1

    sources = pal.parse_sources_arg(args.sources)
    cwd = (
        None
        if args.all_projects
        else (args.cwd.resolve() if args.cwd else Path.cwd().resolve())
    )
    refs = pal.discover_sessions(sources, cwd=cwd, explicit_dir=args.dir)
    if not refs:
        print(
            "No sessions found.\n"
            "  Cursor ~/.cursor/projects/<encoded>/agent-transcripts/\n"
            "  Claude ~/.claude/projects/<encoded>/\n"
            "  Codex  ~/.codex/sessions/**/rollout-*.jsonl\n"
            "  Other  ~/.gemini ~/.continue ~/.windsurf $DOSU_AGENT_LOG_DIRS\n"
            "  or pass --dir / --all-projects",
            file=sys.stderr,
        )
        payload = {
            "generated_at": datetime.now(tz=UTC).isoformat(),
            "window": {"days": args.days},
            "calls": [],
            "totals": {"calls": 0, "sessions": 0, "workspaces": 0},
        }
        if args.out:
            args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        if args.json:
            print(json.dumps(payload, indent=2))
        return 0

    cutoff = datetime.now(tz=UTC) - timedelta(days=args.days) if args.days else None
    calls: list[dict[str, Any]] = []
    for ref in refs:
        if ref.is_subagent and not args.include_subagents:
            continue
        mtime = datetime.fromtimestamp(ref.path.stat().st_mtime, tz=UTC)
        if cutoff is not None and mtime < cutoff:
            continue
        extracted = extract_from_jsonl(ref.path, ref.source, ref.session_id)
        for row in extracted["calls"]:
            when = _call_when(row)
            if cutoff is not None and when is not None and when < cutoff:
                continue
            calls.append(row)

    sessions = {c["transcript_id"] for c in calls}
    workspaces = {c["cwd"] for c in calls if c.get("cwd")}
    hints = Counter(c.get("hint") or "unknown" for c in calls)
    by_source = Counter(c["source"] for c in calls)
    end = datetime.now(tz=UTC)
    start = end - timedelta(days=args.days) if args.days else None
    payload = {
        "generated_at": end.isoformat(),
        "window": {
            "days": args.days,
            "start": start.date().isoformat() if start else None,
            "end": end.date().isoformat(),
        },
        "scope": {
            "all_projects": bool(args.all_projects),
            "sources": list(sources),
        },
        "totals": {
            "calls": len(calls),
            "sessions": len(sessions),
            "workspaces": len(workspaces),
            "by_source": dict(by_source),
            "by_hint": dict(hints),
        },
        "calls": calls,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {args.out}", file=sys.stderr)
    summary = (
        f"calls: {len(calls)}  sessions: {len(sessions)}  "
        f"workspaces: {len(workspaces)}  hints: {dict(hints)}"
    )
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
