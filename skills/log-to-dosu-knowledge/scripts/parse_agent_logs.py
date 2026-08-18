#!/usr/bin/env python3
"""Inventory agent histories from Cursor, Claude Code, and Codex.

Discovers JSONL session logs across all three hosts, normalizes them into one
inventory, estimates (or reads reported) tokens, and ranks sessions for mining
by candidate_score (highest first).

First-time-user logs are assumed to predate Dosu. Rank by size and exploration
only.

Usage:
  python3 parse_agent_logs.py
  python3 parse_agent_logs.py --out /tmp/inventory.json
  python3 parse_agent_logs.py --days 30          # all sessions in past 30 days
  python3 parse_agent_logs.py --full             # full audit (no cap)
  python3 parse_agent_logs.py --limit 20         # N most recent
  python3 parse_agent_logs.py --sources codex
  python3 parse_agent_logs.py --dir ~/.cursor/projects/<proj>/agent-transcripts
  python3 parse_agent_logs.py --digest <id>
  python3 parse_agent_logs.py --self-test
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from activity_buckets import ToolBucketMap, accumulate_jsonl_obj, empty_buckets

CHARS_PER_TOKEN = 4.0
Source = Literal["cursor", "claude", "codex"]
ALL_SOURCES: tuple[Source, ...] = ("cursor", "claude", "codex")

KNOWLEDGE_TOOLS = frozenset(
    {
        "read_knowledge",
        "write_knowledge",
        "review_knowledge",
        "finalize_session_knowledge",
        "init_knowledge",
        "read_org_knowledge",
        "write_org_knowledge",
    }
)

# Cursor tool names + Claude Code tool names + Codex function names
REDISCOVERY_TOOLS = frozenset(
    {
        # Cursor
        "Read",
        "Grep",
        "Glob",
        "Shell",
        "WebSearch",
        "WebFetch",
        "Task",
        "SemanticSearch",
        # Claude Code
        "Bash",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        "Agent",
        # Codex
        "exec_command",
        "write_stdin",
        "web_search",
        "web_search_call",
        "open_page",
        "apply_patch",
        "update_plan",
        "list_dir",
        "grep_files",
        "read_file",
    }
)

USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE
)


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, int(round(len(text) / CHARS_PER_TOKEN)))


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def encode_project_path(cwd: Path) -> str:
    """Claude/Cursor-style project folder: absolute path with / → -."""
    return str(cwd.resolve()).lstrip("/").replace("/", "-")


def unique_resolved_paths(paths: Iterable[Path]) -> list[Path]:
    """Deduplicate paths by resolved location, preserving first-seen order."""
    seen: set[Path] = set()
    out: list[Path] = []
    for path in paths:
        key = path.resolve()
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRef:
    source: Source
    path: Path
    session_id: str
    is_subagent: bool = False


def claude_config_dir() -> Path:
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".claude"


def discover_cursor(cwd: Path | None) -> list[SessionRef]:
    env = os.environ.get("CURSOR_AGENT_TRANSCRIPTS_DIR")
    roots: list[Path] = []
    if env:
        roots.append(Path(env).expanduser())
    else:
        home = Path.home() / ".cursor" / "projects"
        if cwd is not None:
            mapped = home / encode_project_path(cwd) / "agent-transcripts"
            if mapped.is_dir():
                roots.append(mapped)
            else:
                # Also try workspace suffix variants
                for p in sorted(
                    home.glob(f"*{cwd.name}*/agent-transcripts"),
                    key=lambda x: x.stat().st_mtime,
                    reverse=True,
                ):
                    roots.append(p)
        if not roots and home.is_dir():
            roots.extend(
                sorted(
                    home.glob("*/agent-transcripts"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:5]
            )

    refs: list[SessionRef] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.is_dir() or root in seen:
            continue
        seen.add(root)
        for path in root.rglob("*.jsonl"):
            refs.append(
                SessionRef(
                    source="cursor",
                    path=path,
                    session_id=path.stem,
                    is_subagent="subagents" in path.parts,
                )
            )
    return refs


def discover_claude(cwd: Path | None) -> list[SessionRef]:
    """Claude Code: ~/.claude/projects/<encoded>/*.jsonl (and optional sessions/)."""
    base = claude_config_dir() / "projects"
    if not base.is_dir():
        return []

    project_dirs: list[Path] = []
    if cwd is not None:
        # Official encoding replaces non-alphanumeric with '-'; some builds
        # only replace '/'. Try both.
        candidates = [
            base / ("-" + encode_project_path(cwd)),
            base / encode_project_path(cwd),
            base / re.sub(r"[^A-Za-z0-9]", "-", str(cwd.resolve())),
        ]
        for c in candidates:
            if c.is_dir():
                project_dirs.append(c)
        # Fuzzy: folder name contains repo basename
        if not project_dirs:
            project_dirs.extend(
                p
                for p in base.iterdir()
                if p.is_dir() and cwd.name.replace("/", "-") in p.name
            )
    else:
        project_dirs = [p for p in base.iterdir() if p.is_dir()]

    # "-" + encode_project_path(cwd) and re.sub(non-alnum) of cwd.resolve()
    # are the same path (leading "/" becomes "-"). Dedup before globbing.
    project_dirs = unique_resolved_paths(project_dirs)

    refs: list[SessionRef] = []
    seen_ref_paths: set[Path] = set()

    def _emit(ref: SessionRef) -> None:
        key = ref.path.resolve()
        if key in seen_ref_paths:
            return
        seen_ref_paths.add(key)
        refs.append(ref)

    for project_dir in project_dirs:
        # Flat layout: <session-id>.jsonl next to optional subagents/
        for path in project_dir.glob("*.jsonl"):
            _emit(
                SessionRef(
                    source="claude",
                    path=path,
                    session_id=path.stem,
                    is_subagent=False,
                )
            )
        # Nested: sessions/<id>.jsonl
        sessions = project_dir / "sessions"
        if sessions.is_dir():
            for path in sessions.glob("*.jsonl"):
                _emit(
                    SessionRef(
                        source="claude",
                        path=path,
                        session_id=path.stem,
                        is_subagent=False,
                    )
                )
        # Subagents: <session-id>/subagents/*.jsonl or subagents/agent-*.jsonl
        for path in project_dir.rglob("*.jsonl"):
            if "subagents" in path.parts:
                _emit(
                    SessionRef(
                        source="claude",
                        path=path,
                        session_id=path.stem,
                        is_subagent=True,
                    )
                )
    return refs


def discover_codex(cwd: Path | None) -> list[SessionRef]:
    """Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl"""
    root = Path.home() / ".codex" / "sessions"
    if not root.is_dir():
        return []
    refs: list[SessionRef] = []
    cwd_resolved = str(cwd.resolve()) if cwd else None
    for path in root.rglob("*.jsonl"):
        session_id = _codex_session_id(path)
        if cwd_resolved and not _codex_matches_cwd(path, cwd_resolved):
            continue
        refs.append(
            SessionRef(
                source="codex",
                path=path,
                session_id=session_id,
                is_subagent=False,
            )
        )
    return refs


def _codex_session_id(path: Path) -> str:
    # rollout-2026-05-22T12-51-25-019e513e-1de0-78d2-83cb-0a4b36f7195a.jsonl
    stem = path.stem
    m = re.search(
        r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
        stem,
        re.I,
    )
    return m.group(1) if m else stem


def _codex_matches_cwd(path: Path, cwd_resolved: str) -> bool:
    try:
        with path.open(encoding="utf-8") as f:
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta":
                    meta_cwd = (obj.get("payload") or {}).get("cwd") or ""
                    return os.path.normpath(meta_cwd) == os.path.normpath(
                        cwd_resolved
                    ) or cwd_resolved in os.path.normpath(meta_cwd)
    except OSError:
        return False
    # No session_meta — include when filtering by cwd would otherwise drop unknowns
    return True


def discover_sessions(
    sources: Iterable[Source],
    *,
    cwd: Path | None,
    explicit_dir: Path | None = None,
) -> list[SessionRef]:
    if explicit_dir is not None:
        root = explicit_dir.expanduser()
        if not root.is_dir():
            raise SystemExit(f"Not a directory: {root}")
        refs: list[SessionRef] = []
        for path in sorted(
            root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            source = detect_source(path)
            refs.append(
                SessionRef(
                    source=source,
                    path=path,
                    session_id=_session_id_for(source, path),
                    is_subagent="subagents" in path.parts,
                )
            )
        return refs

    refs = []
    src_set = set(sources)
    if "cursor" in src_set:
        refs.extend(discover_cursor(cwd))
    if "claude" in src_set:
        refs.extend(discover_claude(cwd))
    if "codex" in src_set:
        refs.extend(discover_codex(cwd))
    # Newest first
    refs.sort(key=lambda r: r.path.stat().st_mtime, reverse=True)
    return refs


def detect_source(path: Path) -> Source:
    """Sniff JSONL format when --dir is used."""
    try:
        with path.open(encoding="utf-8") as f:
            for _ in range(30):
                line = f.readline()
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session_meta" or (
                    obj.get("type") == "response_item" and "payload" in obj
                ):
                    return "codex"
                if obj.get("type") in {"user", "assistant", "system"} and (
                    "sessionId" in obj or "parentUuid" in obj or "cwd" in obj
                ):
                    return "claude"
                if "role" in obj and "message" in obj:
                    return "cursor"
                if obj.get("type") in {
                    "file-history-snapshot",
                    "summary",
                    "attachment",
                }:
                    return "claude"
    except OSError:
        pass
    # Path heuristics
    parts = {p.lower() for p in path.parts}
    if "agent-transcripts" in parts or ".cursor" in parts:
        return "cursor"
    if ".codex" in parts or path.name.startswith("rollout-"):
        return "codex"
    if ".claude" in parts:
        return "claude"
    return "cursor"


def _session_id_for(source: Source, path: Path) -> str:
    if source == "codex":
        return _codex_session_id(path)
    return path.stem


# ---------------------------------------------------------------------------
# Shared summary + scoring
# ---------------------------------------------------------------------------


@dataclass
class TranscriptSummary:
    transcript_id: str
    source: Source
    path: str
    mtime_iso: str
    bytes: int
    messages: int
    user_messages: int
    assistant_messages: int
    estimated_tokens: int
    reported_tokens: int | None
    user_query_tokens: int
    assistant_tokens: int
    tool_use_tokens: int
    tool_counts: dict[str, int]
    rediscovery_tool_calls: int
    knowledge_reads: int
    knowledge_writes: int
    already_wrote_knowledge: bool
    user_queries: list[str] = field(default_factory=list)
    write_knowledge_calls: list[dict[str, Any]] = field(default_factory=list)
    read_knowledge_calls: list[dict[str, Any]] = field(default_factory=list)
    candidate_score: float = 0.0
    candidate_reasons: list[str] = field(default_factory=list)
    is_subagent: bool = False
    cwd: str | None = None
    git_branch: str | None = None
    token_basis: str = "chars/4"
    context_tokens: int = 0
    code_tokens: int = 0
    planning_tokens: int = 0
    other_tokens: int = 0
    learning_tokens: int = 0
    skip_mine: bool = False
    skip_reason: str | None = None


def effective_tokens(s: TranscriptSummary) -> int:
    if s.reported_tokens is not None and s.reported_tokens > 0:
        return s.reported_tokens
    return s.estimated_tokens


_PUBLIC_SUMMARY_OMIT = (
    "knowledge_reads",
    "knowledge_writes",
    "already_wrote_knowledge",
    "write_knowledge_calls",
    "read_knowledge_calls",
)


def public_summary(s: TranscriptSummary) -> dict[str, Any]:
    """asdict a transcript without prior knowledge I/O for the mining agent."""
    d = asdict(s)
    for key in _PUBLIC_SUMMARY_OMIT:
        d.pop(key, None)
    return d


def score_candidate(s: TranscriptSummary) -> None:
    reasons: list[str] = []
    score = 0.0
    if s.is_subagent:
        s.candidate_score = 0.0
        s.candidate_reasons = ["subagent (skip by default; parent chat owns write)"]
        return
    tok = effective_tokens(s)
    if tok >= 8000:
        score += min(tok / 8000.0, 4.0)
        reasons.append(f"large session (~{tok} tok)")
    if s.rediscovery_tool_calls >= 15:
        score += min(s.rediscovery_tool_calls / 15.0, 3.0)
        reasons.append(f"{s.rediscovery_tool_calls} rediscovery tool calls")
    if s.rediscovery_tool_calls >= 8:
        score += 2.0
        reasons.append("exploration worth caching")
    joined = " ".join(s.user_queries).lower()
    if any(
        w in joined
        for w in (
            "investigate",
            "debug",
            "why",
            "root cause",
            "how does",
            "race",
            "failing",
            "broken",
        )
    ):
        score += 1.0
        reasons.append("investigation-flavored user query")
    s.candidate_score = round(score, 2)
    s.candidate_reasons = reasons


def extract_user_queries(text: str) -> list[str]:
    matches = USER_QUERY_RE.findall(text)
    if matches:
        return [m.strip() for m in matches if m.strip()]
    cleaned = re.sub(r"<timestamp>.*?</timestamp>", "", text, flags=re.DOTALL).strip()
    # Drop injected AGENTS.md / system dumps
    if cleaned.startswith("# AGENTS.md") or cleaned.startswith("<INSTRUCTIONS>"):
        return []
    if cleaned.startswith("<permissions instructions>"):
        return []
    return [cleaned] if cleaned else []


def _knowledge_from_name_and_input(name: str | None, inp: Any) -> dict[str, Any] | None:
    if not name:
        return None
    # Direct tool name
    if name in KNOWLEDGE_TOOLS:
        return {"tool": name, "arguments": inp if isinstance(inp, dict) else {}}
    # MCP wrappers
    if name in {
        "CallMcpTool",
        "mcp__dosu__write_knowledge",
        "mcp__dosu__read_knowledge",
    }:
        if name.startswith("mcp__"):
            tool = name.split("__")[-1]
            return {"tool": tool, "arguments": inp if isinstance(inp, dict) else {}}
        if isinstance(inp, dict):
            tool_name = inp.get("toolName") or inp.get("tool_name") or inp.get("name")
            if tool_name in KNOWLEDGE_TOOLS:
                return {
                    "tool": tool_name,
                    "server": inp.get("server"),
                    "arguments": inp.get("arguments") or inp.get("input") or {},
                }
    # Claude MCP style: mcp__server__tool
    if name.startswith("mcp__") and "__" in name[5:]:
        tool = name.rsplit("__", 1)[-1]
        if tool in KNOWLEDGE_TOOLS:
            return {"tool": tool, "arguments": inp if isinstance(inp, dict) else {}}
    # Codex / generic: function name contains knowledge tool
    for kt in KNOWLEDGE_TOOLS:
        if kt in name:
            args = inp
            if isinstance(inp, str):
                try:
                    args = json.loads(inp)
                except json.JSONDecodeError:
                    args = {"raw": inp}
            return {"tool": kt, "arguments": args if isinstance(args, dict) else {}}
    return None


def _empty_accum() -> dict[str, Any]:
    return {
        "text_chars": 0,
        "user_chars": 0,
        "assistant_chars": 0,
        "tool_chars": 0,
        "messages": 0,
        "user_messages": 0,
        "assistant_messages": 0,
        "tool_counts": Counter(),
        "rediscovery": 0,
        "knowledge_reads": 0,
        "knowledge_writes": 0,
        "user_queries": [],
        "write_calls": [],
        "read_calls": [],
        "reported_tokens": None,
        "cwd": None,
        "git_branch": None,
        "token_basis": "chars/4",
        "usage_by_message_id": {},
        "context_chars": 0,
        "code_chars": 0,
        "tool_map": ToolBucketMap(),
        "buckets": empty_buckets(),
    }


def _finalize(
    path: Path,
    source: Source,
    session_id: str,
    acc: dict[str, Any],
    *,
    is_subagent: bool,
) -> TranscriptSummary:
    text_chars = acc["text_chars"]
    est = int(round(text_chars / CHARS_PER_TOKEN)) if text_chars else 0
    reported = acc["reported_tokens"]
    usage_map: dict[str, int] = acc.get("usage_by_message_id") or {}
    if usage_map and reported is None:
        reported = sum(usage_map.values())
        acc["token_basis"] = "claude usage (deduped by message.id)"
    summary = TranscriptSummary(
        transcript_id=session_id,
        source=source,
        path=str(path),
        mtime_iso=_iso_mtime(path),
        bytes=path.stat().st_size,
        messages=acc["messages"],
        user_messages=acc["user_messages"],
        assistant_messages=acc["assistant_messages"],
        estimated_tokens=est,
        reported_tokens=reported,
        user_query_tokens=int(round(acc["user_chars"] / CHARS_PER_TOKEN)),
        assistant_tokens=int(round(acc["assistant_chars"] / CHARS_PER_TOKEN)),
        tool_use_tokens=int(round(acc["tool_chars"] / CHARS_PER_TOKEN)),
        tool_counts=dict(acc["tool_counts"].most_common()),
        rediscovery_tool_calls=acc["rediscovery"],
        knowledge_reads=acc["knowledge_reads"],
        knowledge_writes=acc["knowledge_writes"],
        already_wrote_knowledge=acc["knowledge_writes"] > 0,
        user_queries=acc["user_queries"][:12],
        write_knowledge_calls=acc["write_calls"],
        read_knowledge_calls=acc["read_calls"],
        is_subagent=is_subagent,
        cwd=acc.get("cwd"),
        git_branch=acc.get("git_branch"),
        token_basis=acc.get("token_basis") or "chars/4",
        context_tokens=(
            int(round(acc.get("context_chars", 0) / CHARS_PER_TOKEN))
            if acc.get("context_chars")
            else 0
        ),
        code_tokens=(
            int(round(acc.get("code_chars", 0) / CHARS_PER_TOKEN))
            if acc.get("code_chars")
            else 0
        ),
        planning_tokens=(
            int(round((acc.get("buckets") or {}).get("planning", 0) / CHARS_PER_TOKEN))
            if (acc.get("buckets") or {}).get("planning")
            else 0
        ),
        other_tokens=(
            int(round((acc.get("buckets") or {}).get("other", 0) / CHARS_PER_TOKEN))
            if (acc.get("buckets") or {}).get("other")
            else 0
        ),
        learning_tokens=(
            int(
                round(
                    (
                        (acc.get("buckets") or {}).get("context", 0)
                        + (acc.get("buckets") or {}).get("planning", 0)
                        + (acc.get("buckets") or {}).get("other", 0)
                    )
                    / CHARS_PER_TOKEN
                )
            )
            if (
                (acc.get("buckets") or {}).get("context", 0)
                + (acc.get("buckets") or {}).get("planning", 0)
                + (acc.get("buckets") or {}).get("other", 0)
            )
            else 0
        ),
    )
    score_candidate(summary)
    return summary


def _note_tool(
    acc: dict[str, Any], name: str | None, inp: Any, payload_text: str
) -> None:
    if not name:
        return
    acc["tool_counts"][name] += 1
    acc["tool_chars"] += len(payload_text)
    if name in REDISCOVERY_TOOLS or any(
        name.startswith(p) for p in ("web_search", "mcp__", "exec_")
    ):
        if name in REDISCOVERY_TOOLS or name.startswith("web_search"):
            acc["rediscovery"] += 1
    kcall = _knowledge_from_name_and_input(name, inp)
    if kcall:
        if kcall["tool"] in {"write_knowledge", "write_org_knowledge"}:
            acc["knowledge_writes"] += 1
            if isinstance(kcall.get("arguments"), dict):
                acc["write_calls"].append(kcall["arguments"])
        elif kcall["tool"] in {
            "read_knowledge",
            "read_org_knowledge",
            "init_knowledge",
        }:
            acc["knowledge_reads"] += 1
            if isinstance(kcall.get("arguments"), dict):
                acc["read_calls"].append(kcall["arguments"])
        acc["tool_counts"][f"mcp:{kcall['tool']}"] += 1


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------


def _attribute_buckets(acc: dict[str, Any], obj: dict[str, Any]) -> None:
    accumulate_jsonl_obj(obj, acc["buckets"], acc["tool_map"])
    acc["context_chars"] = acc["buckets"]["context"]
    acc["code_chars"] = acc["buckets"]["code"]


def parse_cursor(
    path: Path, session_id: str, *, is_subagent: bool
) -> TranscriptSummary:
    acc = _empty_accum()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            _attribute_buckets(acc, obj)
            role = obj.get("role")
            if role is None and obj.get("type") in {"turn_ended", "error"}:
                continue
            acc["messages"] += 1
            content = (obj.get("message") or {}).get("content")
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
                if btype == "text":
                    text = block.get("text") or ""
                    texts.append(text)
                    acc["text_chars"] += len(text)
                    if role == "user":
                        acc["user_chars"] += len(text)
                    elif role == "assistant":
                        acc["assistant_chars"] += len(text)
                elif btype == "tool_use":
                    name = block.get("name")
                    inp = block.get("input")
                    try:
                        payload = json.dumps(
                            {"name": name, "input": inp},
                            ensure_ascii=False,
                            default=str,
                        )
                    except TypeError:
                        payload = str(inp)
                    acc["text_chars"] += len(payload)
                    _note_tool(acc, name, inp, payload)
                elif btype == "tool_result":
                    c = block.get("content")
                    chunk = c if isinstance(c, str) else json.dumps(c, default=str)
                    acc["text_chars"] += len(chunk)
                    acc["tool_chars"] += len(chunk)

            if role == "user":
                acc["user_messages"] += 1
                joined = "\n".join(texts)
                acc["user_queries"].extend(
                    extract_user_queries(joined) if joined else []
                )
            elif role == "assistant":
                acc["assistant_messages"] += 1
    return _finalize(path, "cursor", session_id, acc, is_subagent=is_subagent)


def parse_claude(
    path: Path, session_id: str, *, is_subagent: bool
) -> TranscriptSummary:
    """Claude Code JSONL: type user|assistant|system with nested message + usage."""
    acc = _empty_accum()
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            _attribute_buckets(acc, obj)
            etype = obj.get("type")
            if obj.get("cwd") and not acc["cwd"]:
                acc["cwd"] = obj.get("cwd")
            if obj.get("gitBranch") and not acc["git_branch"]:
                acc["git_branch"] = obj.get("gitBranch")
            if etype not in {"user", "assistant", "system"}:
                continue
            if obj.get("isSidechain") and not is_subagent:
                # Sidechain lines in older layouts — skip for parent scoring
                continue
            acc["messages"] += 1
            msg = obj.get("message") or {}
            content = msg.get("content")
            role = msg.get("role") or etype

            # Dedupe usage by message.id
            usage = msg.get("usage") or obj.get("usage")
            mid = msg.get("id")
            if usage and mid:
                total = int(usage.get("input_tokens") or 0) + int(
                    usage.get("output_tokens") or 0
                )
                # Prefer last (finalized) value
                acc["usage_by_message_id"][mid] = total

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
                    text = block.get("text") or ""
                    texts.append(text)
                    acc["text_chars"] += len(text)
                    if role == "user" or etype == "user":
                        acc["user_chars"] += len(text)
                    else:
                        acc["assistant_chars"] += len(text)
                elif btype == "tool_use":
                    name = block.get("name")
                    inp = block.get("input")
                    payload = json.dumps(
                        {"name": name, "input": inp}, ensure_ascii=False, default=str
                    )
                    acc["text_chars"] += len(payload)
                    _note_tool(acc, name, inp, payload)
                elif btype == "tool_result":
                    c = block.get("content")
                    chunk = c if isinstance(c, str) else json.dumps(c, default=str)
                    acc["text_chars"] += len(chunk)
                    acc["tool_chars"] += len(chunk)

            if etype == "user" or role == "user":
                # Tool results are also type=user — only count human prompts
                if blocks and all(
                    isinstance(b, dict) and b.get("type") == "tool_result"
                    for b in blocks
                ):
                    pass
                else:
                    acc["user_messages"] += 1
                    joined = "\n".join(texts)
                    if joined:
                        acc["user_queries"].extend(
                            extract_user_queries(joined) or [joined[:2000]]
                        )
            elif etype == "assistant" or role == "assistant":
                acc["assistant_messages"] += 1
    return _finalize(path, "claude", session_id, acc, is_subagent=is_subagent)


def parse_codex(path: Path, session_id: str, *, is_subagent: bool) -> TranscriptSummary:
    """Codex rollout JSONL: session_meta / response_item / event_msg."""
    acc = _empty_accum()
    last_total_tokens: int | None = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            _attribute_buckets(acc, obj)
            etype = obj.get("type")
            payload = obj.get("payload") or {}

            if etype == "session_meta":
                acc["cwd"] = payload.get("cwd")
                git = payload.get("git") or {}
                if isinstance(git, dict):
                    acc["git_branch"] = git.get("branch") or git.get("current_branch")
                continue

            if etype == "event_msg":
                ptype = payload.get("type")
                if ptype == "user_message":
                    msg = payload.get("message") or ""
                    acc["messages"] += 1
                    acc["user_messages"] += 1
                    acc["user_chars"] += len(msg)
                    acc["text_chars"] += len(msg)
                    if msg.strip():
                        acc["user_queries"].extend(
                            extract_user_queries(msg) or [msg.strip()]
                        )
                elif ptype == "agent_message":
                    msg = payload.get("message") or ""
                    acc["messages"] += 1
                    acc["assistant_messages"] += 1
                    acc["assistant_chars"] += len(msg)
                    acc["text_chars"] += len(msg)
                elif ptype == "token_count":
                    info = payload.get("info") or {}
                    total = (info.get("total_token_usage") or {}).get("total_tokens")
                    if total is not None:
                        last_total_tokens = int(total)
                        acc["token_basis"] = "codex event_msg.token_count"
                continue

            if etype != "response_item":
                continue

            ptype = payload.get("type")
            if ptype == "message":
                role = payload.get("role")
                # Skip developer / system dumps for query extraction but count tokens
                content = payload.get("content") or []
                texts: list[str] = []
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    text = block.get("text") or ""
                    texts.append(text)
                    acc["text_chars"] += len(text)
                    if role == "user":
                        acc["user_chars"] += len(text)
                    elif role == "assistant":
                        acc["assistant_chars"] += len(text)
                acc["messages"] += 1
                if role == "user":
                    acc["user_messages"] += 1
                    joined = "\n".join(texts)
                    qs = extract_user_queries(joined)
                    if qs:
                        acc["user_queries"].extend(qs)
                    elif joined and not joined.startswith("# AGENTS.md"):
                        # Prefer event_msg user_message; keep short unique prompts only
                        if len(joined) < 2000 and "<INSTRUCTIONS>" not in joined:
                            acc["user_queries"].append(joined.strip())
                elif role == "assistant":
                    acc["assistant_messages"] += 1
            elif ptype == "function_call":
                name = payload.get("name")
                raw_args = payload.get("arguments")
                try:
                    inp = (
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    )
                except json.JSONDecodeError:
                    inp = {"raw": raw_args}
                payload_text = json.dumps(
                    {"name": name, "arguments": inp}, ensure_ascii=False, default=str
                )
                acc["text_chars"] += len(payload_text)
                acc["messages"] += 1
                _note_tool(acc, name, inp, payload_text)
            elif ptype == "function_call_output":
                out = payload.get("output") or ""
                if not isinstance(out, str):
                    out = json.dumps(out, default=str)
                acc["text_chars"] += len(out)
                acc["tool_chars"] += len(out)
            elif ptype == "web_search_call":
                acc["messages"] += 1
                _note_tool(
                    acc, "web_search_call", payload.get("action"), json.dumps(payload)
                )

    if last_total_tokens is not None:
        acc["reported_tokens"] = last_total_tokens
    # Dedupe identical user prompts (event_msg + response_item both record them)
    seen_q: set[str] = set()
    deduped: list[str] = []
    for q in acc["user_queries"]:
        key = q.strip()
        if key and key not in seen_q:
            seen_q.add(key)
            deduped.append(q)
    acc["user_queries"] = deduped
    return _finalize(path, "codex", session_id, acc, is_subagent=is_subagent)


def parse_session(ref: SessionRef) -> TranscriptSummary:
    if ref.source == "cursor":
        return parse_cursor(ref.path, ref.session_id, is_subagent=ref.is_subagent)
    if ref.source == "claude":
        return parse_claude(ref.path, ref.session_id, is_subagent=ref.is_subagent)
    if ref.source == "codex":
        return parse_codex(ref.path, ref.session_id, is_subagent=ref.is_subagent)
    raise ValueError(f"unknown source {ref.source}")


# ---------------------------------------------------------------------------
# Digest
# ---------------------------------------------------------------------------


def build_digest(ref: SessionRef, *, max_assistant_chars: int = 4000) -> dict[str, Any]:
    summary = parse_session(ref)
    turns: list[dict[str, Any]] = []

    if ref.source == "cursor":
        turns = _digest_cursor(ref.path, max_assistant_chars=max_assistant_chars)
    elif ref.source == "claude":
        turns = _digest_claude(ref.path, max_assistant_chars=max_assistant_chars)
    else:
        turns = _digest_codex(ref.path, max_assistant_chars=max_assistant_chars)

    return {"summary": public_summary(summary), "turns": turns}


def _digest_cursor(path: Path, *, max_assistant_chars: int) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = obj.get("role")
            if role is None:
                continue
            content = (obj.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            texts, tools = _blocks_to_digest(
                content, max_assistant_chars if role == "assistant" else 2000
            )
            if role == "user" and texts:
                qs = extract_user_queries("\n".join(texts))
                texts = qs or texts
            if texts or tools:
                turns.append(
                    {
                        "line": line_no,
                        "role": role,
                        "text": texts,
                        "tools": tools,
                        "est_tokens": estimate_tokens(
                            "\n".join(texts) + json.dumps(tools, default=str)
                        ),
                    }
                )
    return turns


def _digest_claude(path: Path, *, max_assistant_chars: int) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = obj.get("type")
            if etype not in {"user", "assistant"}:
                continue
            msg = obj.get("message") or {}
            content = msg.get("content")
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            if not isinstance(content, list):
                continue
            # Skip pure tool_result user rows in digest text focus — still show tools
            texts, tools = _blocks_to_digest(
                content, max_assistant_chars if etype == "assistant" else 2000
            )
            if etype == "user" and texts:
                qs = extract_user_queries("\n".join(texts))
                texts = qs or texts
            if texts or tools:
                turns.append(
                    {
                        "line": line_no,
                        "role": etype,
                        "text": texts,
                        "tools": tools,
                        "est_tokens": estimate_tokens(
                            "\n".join(texts) + json.dumps(tools, default=str)
                        ),
                    }
                )
    return turns


def _digest_codex(path: Path, *, max_assistant_chars: int) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            etype = obj.get("type")
            payload = obj.get("payload") or {}
            if etype == "event_msg" and payload.get("type") == "user_message":
                msg = (payload.get("message") or "")[:2000]
                turns.append(
                    {
                        "line": line_no,
                        "role": "user",
                        "text": [msg],
                        "tools": [],
                        "est_tokens": estimate_tokens(msg),
                    }
                )
            elif etype == "event_msg" and payload.get("type") == "agent_message":
                msg = payload.get("message") or ""
                if len(msg) > max_assistant_chars:
                    msg = msg[:max_assistant_chars] + "\n…[truncated]"
                turns.append(
                    {
                        "line": line_no,
                        "role": "assistant",
                        "text": [msg],
                        "tools": [],
                        "est_tokens": estimate_tokens(msg),
                    }
                )
            elif etype == "response_item" and payload.get("type") == "function_call":
                name = payload.get("name")
                args = payload.get("arguments")
                preview = (
                    args[:160]
                    if isinstance(args, str)
                    else json.dumps(args, default=str)[:160]
                )
                turns.append(
                    {
                        "line": line_no,
                        "role": "assistant",
                        "text": [],
                        "tools": [{"name": name, "command_preview": preview}],
                        "est_tokens": estimate_tokens(preview),
                    }
                )
    return turns


_MCP_DIGEST_TOOLS = frozenset({"CallMcpTool", "GetMcpTools"})


def _copy_mcp_digest_fields(
    entry: dict[str, Any], name: str | None, inp: Any
) -> None:
    """Copy MCP identity + args so the report can label non-knowledge calls."""
    if not isinstance(inp, dict):
        return
    is_mcp = name in _MCP_DIGEST_TOOLS or bool(inp.get("toolName") or inp.get("server"))
    if not is_mcp:
        return
    tool_name = inp.get("toolName") or inp.get("tool_name")
    if tool_name:
        entry["toolName"] = tool_name
    server = inp.get("server")
    if server:
        entry["server"] = server
    if name == "GetMcpTools" and inp.get("pattern"):
        entry["pattern"] = inp["pattern"]
    args = inp.get("arguments")
    if isinstance(args, dict):
        entry["arguments"] = args


def _blocks_to_digest(
    content: list[Any], max_text_chars: int
) -> tuple[list[str], list[dict[str, Any]]]:
    texts: list[str] = []
    tools: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in {"text", "input_text", "output_text"} and block.get("text"):
            t = block["text"]
            if len(t) > max_text_chars:
                t = t[:max_text_chars] + "\n…[truncated]"
            texts.append(t)
        elif btype == "tool_use":
            name = block.get("name")
            entry: dict[str, Any] = {"name": name}
            inp = block.get("input") or {}
            kcall = _knowledge_from_name_and_input(name, inp)
            if kcall:
                entry["knowledge"] = kcall
            elif isinstance(inp, dict):
                if "path" in inp:
                    entry["path"] = inp.get("path")
                if "pattern" in inp:
                    entry["pattern"] = inp.get("pattern")
                if "command" in inp:
                    entry["command_preview"] = str(inp.get("command"))[:160]
            _copy_mcp_digest_fields(entry, name, inp)
            tools.append(entry)
        elif btype == "tool_result":
            tools.append({"name": "tool_result"})
    return texts, tools


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


BOOTSTRAP_PHRASES = (
    "please bootstrap my knowledge with dosu",
    "/log-to-dosu-knowledge",
    "/bootstrap-agent-knowledge",
    "mine my agent logs into dosu",
)

# Follow-ups that are still the harvest skill, not a new task.
HARVEST_FOLLOWUP_MARKERS = (
    "log-to-dosu",
    "bootstrap my knowledge",
    "write_knowledge",
    "digest",
    "inventory",
)


def _is_short_bootstrap_prompt(q: str) -> bool:
    text = (q or "").strip()
    if not text or len(text) > 400:
        return False
    lowered = text.lower()
    return any(phrase in lowered for phrase in BOOTSTRAP_PHRASES)


def _is_harvest_followup(q: str) -> bool:
    """True when a later query is still the harvest, not a different task."""
    if _is_short_bootstrap_prompt(q):
        return True
    lowered = (q or "").strip().lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in HARVEST_FOLLOWUP_MARKERS)


def is_bootstrap_only_session(s: TranscriptSummary) -> bool:
    """True when the first query starts a harvest and later ones stay on it.

    The CLI handoff line ("Please bootstrap my knowledge with Dosu from my
    local agent logs.") matches BOOTSTRAP_PHRASES. Follow-ups that still
    mention digest/inventory/write_knowledge/log-to-dosu do not disqualify.
    A later query that is a clearly different task does.
    """
    queries = [q for q in (s.user_queries or []) if str(q).strip()]
    if not queries:
        return False
    if not _is_short_bootstrap_prompt(queries[0]):
        return False
    return all(_is_harvest_followup(q) for q in queries[1:])


def find_ref(refs: list[SessionRef], session_id: str) -> SessionRef:
    matches = [
        r for r in refs if r.session_id == session_id or r.path.stem == session_id
    ]
    if not matches:
        # Also allow prefix / substring
        matches = [
            r for r in refs if session_id in r.session_id or session_id in str(r.path)
        ]
    if not matches:
        raise SystemExit(
            f"No session matching {session_id!r}. Run without --digest to list."
        )
    matches.sort(key=lambda r: (r.is_subagent, -r.path.stat().st_mtime))
    return matches[0]


def print_table(summaries: list[TranscriptSummary]) -> None:
    print(
        f"{'Src':<7} {'ID':<38} {'Learn':>8} {'Rdisc':>6} {'Score':>6}  Query"
    )
    print("-" * 120)
    for s in summaries:
        tok = s.learning_tokens
        marker = "*" if s.skip_mine else " "
        q = s.user_queries[0][:55].replace("\n", " ") if s.user_queries else ""
        print(
            f"{s.source:<7} {s.transcript_id:<38} {tok:>7}{marker} {s.rediscovery_tool_calls:>6} "
            f"{s.candidate_score:>6}  {q}"
        )
    print("(* = skip_mine; Learn = cost to learn, excludes code)")


def parse_sources_arg(raw: str) -> list[Source]:
    parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    out: list[Source] = []
    for p in parts:
        if p not in ALL_SOURCES:
            raise SystemExit(
                f"Unknown source {p!r}. Choose from: {', '.join(ALL_SOURCES)}"
            )
        out.append(p)  # type: ignore[arg-type]
    return out or list(ALL_SOURCES)



def resolve_inventory_scope(
    *,
    full: bool,
    days: int | None,
    limit: int | None,
) -> dict[str, Any]:
    """Resolve inventory window.

    Defaults to the 50 most recent sessions. `--days N` keeps every session in
    that window (no count cap unless `--limit` is also set). `--full` is a full
    audit with no time or count filter.
    """
    if full and days is not None:
        raise SystemExit("Use either --full or --days, not both")
    if full:
        return {
            "mode": "full",
            "limit": None,
            "days": None,
            "cutoff_mtime": None,
        }
    if days is not None:
        if days <= 0:
            raise SystemExit("--days must be a positive integer")
        cutoff = datetime.now(tz=UTC) - timedelta(days=days)
        return {
            "mode": "days",
            "limit": limit if limit is not None and limit > 0 else None,
            "days": days,
            "cutoff_mtime": cutoff,
        }
    # Default / explicit --limit: most recent N by mtime (default 50).
    n = 50 if limit is None else limit
    if n <= 0:
        raise SystemExit("--limit must be a positive integer (or omit for default 50)")
    return {
        "mode": "recent",
        "limit": n,
        "days": None,
        "cutoff_mtime": None,
    }


def run_self_test() -> int:
    """Tiny fixtures for cursor + claude + codex parsers."""
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
                                        "text": "<user_query>investigate the race</user_query>",
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
                                    {"type": "text", "text": "Looking into it."},
                                    {
                                        "type": "tool_use",
                                        "id": "grep-1",
                                        "name": "Grep",
                                        "input": {"pattern": "UniqueViolation"},
                                    },
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
                                        "tool_use_id": "grep-1",
                                        "content": "UniqueViolation match in foo.py\n" * 20,
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

        claude = root / "claude.jsonl"
        claude.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "user",
                            "cwd": "/tmp/demo",
                            "gitBranch": "main",
                            "sessionId": "claude-1",
                            "message": {"role": "user", "content": "debug flaky test"},
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "sessionId": "claude-1",
                            "message": {
                                "id": "msg_1",
                                "role": "assistant",
                                "content": [
                                    {"type": "text", "text": "Running tests."},
                                    {
                                        "type": "tool_use",
                                        "name": "Bash",
                                        "input": {"command": "pytest"},
                                    },
                                ],
                                "usage": {
                                    "input_tokens": 1000,
                                    "output_tokens": 50,
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "assistant",
                            "sessionId": "claude-1",
                            "message": {
                                "id": "msg_1",
                                "role": "assistant",
                                "content": [
                                    {
                                        "type": "tool_use",
                                        "name": "mcp__dosu__write_knowledge",
                                        "input": {
                                            "title": "flaky test",
                                            "content": "retry on UniqueViolation",
                                        },
                                    }
                                ],
                                "usage": {
                                    "input_tokens": 1000,
                                    "output_tokens": 50,
                                },
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        codex = root / "codex.jsonl"
        codex.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "type": "session_meta",
                            "payload": {
                                "id": "019e513e-1de0-78d2-83cb-0a4b36f7195a",
                                "cwd": "/tmp/demo",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "user_message",
                                "message": "why is deploy failing?",
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "response_item",
                            "payload": {
                                "type": "function_call",
                                "name": "exec_command",
                                "arguments": json.dumps({"cmd": "rg deploy"}),
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "type": "event_msg",
                            "payload": {
                                "type": "token_count",
                                "info": {"total_token_usage": {"total_tokens": 41236}},
                            },
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        c = parse_session(SessionRef("cursor", cursor, "cursor", False))
        cl = parse_session(SessionRef("claude", claude, "claude-1", False))
        cx = parse_session(
            SessionRef("codex", codex, "019e513e-1de0-78d2-83cb-0a4b36f7195a", False)
        )

        assert "investigate the race" in (c.user_queries[0] if c.user_queries else "")
        assert c.rediscovery_tool_calls >= 1
        assert c.context_tokens > 0, c.context_tokens
        assert c.learning_tokens >= c.context_tokens, (
            c.learning_tokens,
            c.context_tokens,
        )
        assert c.learning_tokens > c.context_tokens, c.learning_tokens
        assert cl.reported_tokens == 1050, cl.reported_tokens  # deduped once
        assert cl.knowledge_writes == 1, cl.knowledge_writes
        assert cl.code_tokens > 0, cl.code_tokens
        assert cx.reported_tokens == 41236, cx.reported_tokens
        assert cx.rediscovery_tool_calls >= 1
        assert detect_source(cursor) == "cursor"
        assert detect_source(claude) == "claude"
        assert detect_source(codex) == "codex"
        # Scope resolution
        s_default = resolve_inventory_scope(full=False, days=None, limit=None)
        assert s_default["mode"] == "recent" and s_default["limit"] == 50
        s_days = resolve_inventory_scope(full=False, days=30, limit=None)
        assert s_days["mode"] == "days" and s_days["limit"] is None
        assert s_days["cutoff_mtime"] is not None
        s_full = resolve_inventory_scope(full=True, days=None, limit=None)
        assert s_full["mode"] == "full" and s_full["limit"] is None
        try:
            resolve_inventory_scope(full=True, days=7, limit=None)
            raise AssertionError("expected --full/--days conflict")
        except SystemExit:
            pass

        # Two Claude encodings of the same cwd resolve to one project dir.
        cwd_demo = Path("/Users/demo/repo")
        twin_a = Path("/tmp/claude-projects") / ("-" + encode_project_path(cwd_demo))
        twin_b = Path("/tmp/claude-projects") / re.sub(
            r"[^A-Za-z0-9]", "-", str(cwd_demo.resolve())
        )
        assert twin_a == twin_b, (twin_a, twin_b)
        assert unique_resolved_paths([twin_a, twin_b]) == [twin_a]

        large = TranscriptSummary(
            transcript_id="large-exploratory",
            source="cursor",
            path="/tmp/large.jsonl",
            mtime_iso="2026-01-01T00:00:00+00:00",
            bytes=80_000,
            messages=40,
            user_messages=8,
            assistant_messages=32,
            estimated_tokens=20_000,
            reported_tokens=None,
            user_query_tokens=400,
            assistant_tokens=8_000,
            tool_use_tokens=11_600,
            tool_counts={"Grep": 12, "Read": 10},
            rediscovery_tool_calls=22,
            knowledge_reads=0,
            knowledge_writes=3,
            already_wrote_knowledge=True,
            user_queries=["investigate the race"],
        )
        score_candidate(large)
        assert not any("already wrote" in r for r in large.candidate_reasons), (
            large.candidate_reasons
        )
        assert not any(
            "never called read_knowledge" in r for r in large.candidate_reasons
        ), large.candidate_reasons
        pub = public_summary(large)
        for key in _PUBLIC_SUMMARY_OMIT:
            assert key not in pub, key

        tiny = TranscriptSummary(
            transcript_id="tiny",
            source="cursor",
            path="/tmp/tiny.jsonl",
            mtime_iso="2026-01-01T00:00:00+00:00",
            bytes=400,
            messages=2,
            user_messages=1,
            assistant_messages=1,
            estimated_tokens=100,
            reported_tokens=None,
            user_query_tokens=40,
            assistant_tokens=60,
            tool_use_tokens=0,
            tool_counts={},
            rediscovery_tool_calls=0,
            knowledge_reads=0,
            knowledge_writes=0,
            already_wrote_knowledge=False,
        )
        score_candidate(tiny)
        assert large.candidate_score > tiny.candidate_score, (
            large.candidate_score,
            tiny.candidate_score,
        )
        assert large.context_tokens == 0 and large.code_tokens == 0
        assert large.learning_tokens == 0 and large.planning_tokens == 0
        assert large.skip_mine is False and large.skip_reason is None

        boot = TranscriptSummary(
            transcript_id="boot",
            source="cursor",
            path="/tmp/boot.jsonl",
            mtime_iso="2026-01-01T00:00:00+00:00",
            bytes=200,
            messages=2,
            user_messages=1,
            assistant_messages=1,
            estimated_tokens=40,
            reported_tokens=None,
            user_query_tokens=20,
            assistant_tokens=20,
            tool_use_tokens=0,
            tool_counts={},
            rediscovery_tool_calls=0,
            knowledge_reads=0,
            knowledge_writes=0,
            already_wrote_knowledge=False,
            user_queries=["Please bootstrap my knowledge with Dosu"],
        )
        assert is_bootstrap_only_session(boot)
        assert is_bootstrap_only_session(
            TranscriptSummary(
                transcript_id="slash",
                source="cursor",
                path="/tmp/slash.jsonl",
                mtime_iso="2026-01-01T00:00:00+00:00",
                bytes=200,
                messages=1,
                user_messages=1,
                assistant_messages=0,
                estimated_tokens=10,
                reported_tokens=None,
                user_query_tokens=10,
                assistant_tokens=0,
                tool_use_tokens=0,
                tool_counts={},
                rediscovery_tool_calls=0,
                knowledge_reads=0,
                knowledge_writes=0,
                already_wrote_knowledge=False,
                user_queries=["/log-to-dosu-knowledge"],
            )
        )
        assert is_bootstrap_only_session(
            TranscriptSummary(
                transcript_id="mine",
                source="cursor",
                path="/tmp/mine.jsonl",
                mtime_iso="2026-01-01T00:00:00+00:00",
                bytes=200,
                messages=1,
                user_messages=1,
                assistant_messages=0,
                estimated_tokens=10,
                reported_tokens=None,
                user_query_tokens=10,
                assistant_tokens=0,
                tool_use_tokens=0,
                tool_counts={},
                rediscovery_tool_calls=0,
                knowledge_reads=0,
                knowledge_writes=0,
                already_wrote_knowledge=False,
                user_queries=["mine my agent logs into Dosu"],
            )
        )
        assert not is_bootstrap_only_session(large)
        assert not is_bootstrap_only_session(tiny)

        cli_handoff = TranscriptSummary(
            transcript_id="cli-handoff",
            source="cursor",
            path="/tmp/cli-handoff.jsonl",
            mtime_iso="2026-01-01T00:00:00+00:00",
            bytes=200,
            messages=2,
            user_messages=1,
            assistant_messages=1,
            estimated_tokens=40,
            reported_tokens=None,
            user_query_tokens=20,
            assistant_tokens=20,
            tool_use_tokens=0,
            tool_counts={},
            rediscovery_tool_calls=0,
            knowledge_reads=0,
            knowledge_writes=0,
            already_wrote_knowledge=False,
            user_queries=[
                "Please bootstrap my knowledge with Dosu from my local agent logs."
            ],
        )
        assert is_bootstrap_only_session(cli_handoff)
        cli_handoff.user_queries.append("also debug the slack picker")
        assert not is_bootstrap_only_session(cli_handoff)
        cli_handoff.user_queries = [
            "Please bootstrap my knowledge with Dosu from my local agent logs.",
            "run the digest for 7b7c08a4",
        ]
        assert is_bootstrap_only_session(cli_handoff)

        _, mcp_tools = _blocks_to_digest(
            [
                {
                    "type": "tool_use",
                    "name": "CallMcpTool",
                    "input": {
                        "server": "user-logfire",
                        "toolName": "query_run",
                        "arguments": {"query": "SELECT 1"},
                    },
                },
                {
                    "type": "tool_use",
                    "name": "GetMcpTools",
                    "input": {"server": "user-supabase", "pattern": "sql"},
                },
                {
                    "type": "tool_use",
                    "name": "CallMcpTool",
                    "input": {
                        "server": "user-dosu",
                        "toolName": "read_knowledge",
                        "arguments": {"query": "UniqueViolation"},
                    },
                },
            ],
            200,
        )
        assert mcp_tools[0]["toolName"] == "query_run"
        assert mcp_tools[0]["server"] == "user-logfire"
        assert mcp_tools[0]["arguments"]["query"] == "SELECT 1"
        assert "knowledge" not in mcp_tools[0]
        assert mcp_tools[1]["pattern"] == "sql"
        assert mcp_tools[1]["server"] == "user-supabase"
        assert mcp_tools[2]["knowledge"]["tool"] == "read_knowledge"
        assert mcp_tools[2]["toolName"] == "read_knowledge"
        assert mcp_tools[2]["arguments"]["query"] == "UniqueViolation"

        print("self-test OK")
        print(
            json.dumps(
                {
                    "cursor_tokens": effective_tokens(c),
                    "claude_tokens": effective_tokens(cl),
                    "claude_basis": cl.token_basis,
                    "codex_tokens": effective_tokens(cx),
                    "codex_basis": cx.token_basis,
                },
                indent=2,
            )
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--sources",
        default="cursor,claude,codex",
        help="comma-separated: cursor,claude,codex (default: all)",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="explicit directory of JSONL files (auto-detect format per file)",
    )
    parser.add_argument(
        "--cwd",
        type=Path,
        default=None,
        help="project cwd used to pick Cursor/Claude project folders and filter Codex (default: process cwd)",
    )
    parser.add_argument(
        "--all-projects", action="store_true", help="do not scope to --cwd"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="max most-recent sessions by mtime (default: 50; ignored by --full "
        "unless combined with --days)",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="only sessions with mtime in the past N days (no count cap unless "
        "--limit is also set)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="full audit: every discovered parent session (no days/limit filter)",
    )
    parser.add_argument("--min-tokens", type=int, default=0)
    parser.add_argument("--include-subagents", action="store_true")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--digest", metavar="ID")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()

    sources = parse_sources_arg(args.sources)
    cwd = (
        None
        if args.all_projects
        else (args.cwd.resolve() if args.cwd else Path.cwd().resolve())
    )

    refs = discover_sessions(sources, cwd=cwd, explicit_dir=args.dir)
    if not refs:
        print(
            "No sessions found.\n"
            f"  sources={','.join(sources)} cwd={cwd}\n"
            "  Cursor: ~/.cursor/projects/<encoded>/agent-transcripts/\n"
            "  Claude: $CLAUDE_CONFIG_DIR/projects/<encoded>/*.jsonl "
            f"(now: {claude_config_dir() / 'projects'})\n"
            "  Codex:  ~/.codex/sessions/**/rollout-*.jsonl\n"
            "Pass --dir or --all-projects, or see references/history-locations.md",
            file=sys.stderr,
        )
        return 1

    if args.digest:
        ref = find_ref(refs, args.digest)
        digest = build_digest(ref)
        if args.json:
            print(json.dumps(digest, indent=2, ensure_ascii=False))
        else:
            s = digest["summary"]
            eff = s.get("reported_tokens") or s.get("estimated_tokens") or 0
            print(f"# Digest [{s['source']}] {s['transcript_id']}")
            print(f"path: {s['path']}")
            print(
                f"tokens: learning={s.get('learning_tokens', 0)} "
                f"context={s.get('context_tokens', 0)} "
                f"effective={eff} estimated={s['estimated_tokens']} "
                f"reported={s.get('reported_tokens')} basis={s.get('token_basis')}"
            )
            print(f"score={s['candidate_score']}")
            print(f"reasons: {s['candidate_reasons']}")
            if s.get("cwd"):
                print(f"cwd: {s['cwd']}  branch: {s.get('git_branch')}")
            print()
            for q in s.get("user_queries") or []:
                preview = q if len(q) <= 1200 else q[:1200] + "\n…[truncated]"
                print(f"## User query\n{preview}\n")
            print("## Turns (truncated)")
            for turn in digest["turns"][:80]:
                tools = ", ".join(t.get("name") or "?" for t in turn.get("tools") or [])
                preview = ""
                if turn.get("text"):
                    preview = turn["text"][0][:140].replace("\n", " ")
                print(
                    f"- L{turn['line']} {turn['role']} ~{turn['est_tokens']}tok "
                    f"tools=[{tools}] {preview}"
                )
        return 0

    scope = resolve_inventory_scope(
        full=args.full, days=args.days, limit=args.limit
    )
    cutoff: datetime | None = scope["cutoff_mtime"]
    limit: int | None = scope["limit"]

    # refs are newest-first by mtime. Apply time window, then optional count cap
    # on most-recent sessions, then parse + rank by candidate score.
    # Recent/--limit mode over-selects (limit*4), drops bootstrap-only, then
    # keeps `limit` mineable sessions. --full keeps bootstrap-only with skip_mine.
    selected: list[SessionRef] = []
    for ref in refs:
        if ref.is_subagent and not args.include_subagents:
            continue
        mtime = datetime.fromtimestamp(ref.path.stat().st_mtime, tz=UTC)
        if cutoff is not None and mtime < cutoff:
            continue
        selected.append(ref)
    recent_mode = scope["mode"] == "recent"
    if limit is not None and recent_mode:
        selected = selected[: limit * 4]
    elif limit is not None:
        selected = selected[:limit]

    summaries: list[TranscriptSummary] = []
    for ref in selected:
        s = parse_session(ref)
        if is_bootstrap_only_session(s):
            if recent_mode:
                continue
            s.skip_mine = True
            s.skip_reason = "bootstrap-only"
        if effective_tokens(s) < args.min_tokens:
            continue
        summaries.append(s)
    if limit is not None and recent_mode:
        summaries = summaries[:limit]

    ranked = sorted(
        summaries,
        key=lambda x: (x.candidate_score, effective_tokens(x)),
        reverse=True,
    )
    total_est = sum(s.estimated_tokens for s in ranked)
    total_eff = sum(effective_tokens(s) for s in ranked)
    total_ctx = sum(s.context_tokens for s in ranked)
    total_code = sum(s.code_tokens for s in ranked)
    total_planning = sum(s.planning_tokens for s in ranked)
    total_other = sum(s.other_tokens for s in ranked)
    total_learn = sum(s.learning_tokens for s in ranked)
    by_source = Counter(s.source for s in ranked)
    summaries = ranked  # inventory uses the trimmed set

    inventory = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "scope": {
            "mode": scope["mode"],
            "limit": scope["limit"],
            "days": scope["days"],
            "cutoff_mtime": (
                scope["cutoff_mtime"].isoformat() if scope["cutoff_mtime"] else None
            ),
        },
        "sources_requested": sources,
        "cwd": str(cwd) if cwd else None,
        "chars_per_token": CHARS_PER_TOKEN,
        "token_note": (
            "Savings baseline is learning_tokens (cost to learn THIS fact: "
            "context + planning + other). Excludes code (Write/Edit/mutating "
            "shell). Never estimated/effective, never context_tokens alone. "
            "effective_tokens still prefers host-reported usage when present."
        ),
        "totals": {
            "transcripts": len(summaries),
            "estimated_tokens": total_est,
            "effective_tokens": total_eff,
            "context_tokens": total_ctx,
            "code_tokens": total_code,
            "planning_tokens": total_planning,
            "other_tokens": total_other,
            "learning_tokens": total_learn,
            "by_source": dict(by_source),
        },
        "transcripts": [public_summary(s) for s in ranked],
    }

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(
            json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        print(f"Wrote {args.out}", file=sys.stderr)

    if args.json:
        print(json.dumps(inventory, indent=2, ensure_ascii=False))
    else:
        scope_bits = [f"mode={scope['mode']}"]
        if scope["days"] is not None:
            scope_bits.append(f"days={scope['days']}")
        if scope["limit"] is not None:
            scope_bits.append(f"limit={scope['limit']}")
        print(f"sources: {', '.join(sources)}  cwd: {cwd}  ({', '.join(scope_bits)})")
        print(
            f"transcripts: {len(summaries)}  learning_tokens: {total_learn}  "
            f"context_tokens: {total_ctx}  "
            f"effective_tokens: {total_eff}  "
            f"estimated_tokens: {total_est}  by_source: {dict(by_source)}"
        )
        print()
        print_table(ranked[:40])
        print()
        print("Next: python3 .../parse_agent_logs.py --digest <id>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
