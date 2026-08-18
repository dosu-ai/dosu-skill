#!/usr/bin/env python3
"""Decant-aligned activity buckets for agent-log token attribution.

https://raw.githubusercontent.com/dosu-ai/decant/main/docs/analytics-methodology.md

Four buckets:

- context: reads, searches, web/MCP retrieval, read-only shell/git
- code: structured edits and mutating / unrecognized shell
- planning: plan-management tools
- other: knowledge writes/review/finalize, plus user/assistant prose

Unknown structured tools default to context (do not overstate implementation).
Unrecognized shell defaults to code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

CHARS_PER_TOKEN = 4.0
Bucket = Literal["context", "code", "planning", "other"]

CONTEXT_TOOLS = frozenset(
    {
        "Read",
        "Grep",
        "Glob",
        "WebSearch",
        "WebFetch",
        "SemanticSearch",
        "Task",
        "Agent",
        "list_dir",
        "grep_files",
        "read_file",
        "web_search",
        "web_search_call",
        "open_page",
        "GetMcpTools",
        "FetchMcpResource",
        "read_knowledge",
        "read_org_knowledge",
        "init_knowledge",
        "whoami",
        "execute_sql",
        "query_run",
        "query_logs",
        "query_schema_reference",
        "list_tables",
        "search_docs",
        "browser_snapshot",
        "browser_tabs",
        "browser_navigate",
    }
)

CODE_TOOLS = frozenset(
    {
        "Write",
        "Edit",
        "MultiEdit",
        "NotebookEdit",
        "EditNotebook",
        "StrReplace",
        "Delete",
        "apply_patch",
        "GenerateImage",
        "write_stdin",
    }
)

PLANNING_TOOLS = frozenset(
    {
        "TodoWrite",
        "update_plan",
        "SwitchMode",
        "AskQuestion",
    }
)

OTHER_TOOLS = frozenset(
    {
        "write_knowledge",
        "write_org_knowledge",
        "review_knowledge",
        "finalize_session_knowledge",
    }
)

SHELL_TOOLS = frozenset({"Shell", "Bash", "exec_command"})

READONLY_SHELL_HEADS = frozenset(
    {
        "rg",
        "grep",
        "egrep",
        "fgrep",
        "ag",
        "ack",
        "cat",
        "head",
        "tail",
        "less",
        "more",
        "bat",
        "ls",
        "ll",
        "find",
        "fd",
        "tree",
        "pwd",
        "which",
        "type",
        "file",
        "stat",
        "wc",
        "du",
        "df",
        "date",
        "whoami",
        "id",
        "uname",
        "env",
        "printenv",
        "echo",
        "printf",
        "true",
        "false",
        "jq",
        "yq",
        "awk",
        "cut",
        "sort",
        "uniq",
        "tr",
        "git",
        "gh",
        "sed",
    }
)

READONLY_GIT_SUBS = frozenset(
    {
        "diff",
        "log",
        "show",
        "status",
        "blame",
        "rev-parse",
        "branch",
        "remote",
        "describe",
        "cat-file",
        "ls-files",
        "ls-tree",
        "name-rev",
        "symbolic-ref",
        "shortlog",
        "rev-list",
        "grep",
    }
)

GIT_CODE_SUBS = frozenset({"commit", "add", "push", "reset"})

SHELL_WRAPPERS = frozenset(
    {
        "sudo",
        "time",
        "nice",
        "nohup",
        "command",
        "builtin",
        "exec",
        "env",
    }
)

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def empty_buckets() -> dict[str, int]:
    return {"context": 0, "code": 0, "planning": 0, "other": 0}


class ToolBucketMap:
    """Pair tool_result / function_call_output with the originating tool_use."""

    def __init__(self) -> None:
        self._ids: dict[str, Bucket] = {}
        self._last: Bucket = "context"

    def record(self, tool_id: str | None, bucket: Bucket) -> None:
        self._last = bucket
        if tool_id:
            self._ids[str(tool_id)] = bucket

    def lookup(self, tool_id: str | None) -> Bucket:
        if tool_id:
            found = self._ids.get(str(tool_id))
            if found is not None:
                return found
            return "context"
        return self._last


def _tokenize(segment: str) -> list[str]:
    tokens: list[str] = []
    buf: list[str] = []
    in_s = in_d = False
    i = 0
    while i < len(segment):
        c = segment[i]
        if c == "'" and not in_d:
            in_s = not in_s
            buf.append(c)
            i += 1
            continue
        if c == '"' and not in_s:
            in_d = not in_d
            buf.append(c)
            i += 1
            continue
        if c.isspace() and not in_s and not in_d:
            if buf:
                tokens.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    if buf:
        tokens.append("".join(buf))
    return tokens


def _split_pipeline(cmd: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    in_s = in_d = False
    i = 0
    while i < len(cmd):
        c = cmd[i]
        if c == "'" and not in_d:
            in_s = not in_s
            buf.append(c)
            i += 1
            continue
        if c == '"' and not in_s:
            in_d = not in_d
            buf.append(c)
            i += 1
            continue
        if not in_s and not in_d:
            if cmd.startswith("||", i) or cmd.startswith("&&", i):
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
                i += 2
                continue
            if c in "|;":
                part = "".join(buf).strip()
                if part:
                    parts.append(part)
                buf = []
                i += 1
                continue
        buf.append(c)
        i += 1
    part = "".join(buf).strip()
    if part:
        parts.append(part)
    return parts


def _basename(token: str) -> str:
    raw = token.strip().strip("'\"")
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]
    return raw


def _peel_wrappers(tokens: list[str]) -> list[str]:
    i = 0
    n = len(tokens)
    while i < n:
        tok = tokens[i]
        if _ENV_ASSIGN_RE.match(tok) and not tok.startswith("-"):
            i += 1
            continue
        head = _basename(tok).lower()
        if head in SHELL_WRAPPERS:
            i += 1
            continue
        return tokens[i:]
    return []


def _first_non_flag(args: list[str]) -> tuple[str, list[str]]:
    i = 0
    while i < len(args):
        a = args[i]
        if a.startswith("-"):
            if a in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
                i += 2
                continue
            i += 1
            continue
        return a, args[i + 1 :]
    return "", []


def _sed_inplace(args: list[str]) -> bool:
    for a in args:
        if a == "-i" or a.startswith("-i") or a == "--in-place" or a.startswith("--in-place="):
            return True
        if a.startswith("-") and not a.startswith("--") and "i" in a[1:]:
            return True
    return False


def _git_bucket(args: list[str]) -> Bucket:
    sub, rest = _first_non_flag(args)
    sub = sub.lower()
    if not sub:
        return "code"
    if sub in GIT_CODE_SUBS:
        return "code"
    if sub == "stash":
        op, _ = _first_non_flag(rest)
        if op.lower() in {"list", "show"}:
            return "context"
        return "code"
    if sub in READONLY_GIT_SUBS:
        return "context"
    return "code"


def _segment_is_context(segment: str) -> bool:
    tokens = _peel_wrappers(_tokenize(segment))
    if not tokens:
        return True
    head = _basename(tokens[0]).lower()
    args = tokens[1:]
    if head not in READONLY_SHELL_HEADS:
        return False
    if head == "git":
        return _git_bucket(args) == "context"
    if head == "sed":
        return not _sed_inplace(args)
    return True


def classify_shell_command(cmd: str | None) -> Bucket:
    """Return context for read-only pipelines, else code."""
    if not cmd or not str(cmd).strip():
        return "code"
    for segment in _split_pipeline(str(cmd)):
        if not _segment_is_context(segment):
            return "code"
    return "context"


def _canonical_tool_name(name: str | None, inp: Any) -> str:
    if not name:
        return ""
    if name == "CallMcpTool" and isinstance(inp, dict):
        inner = inp.get("toolName") or inp.get("tool_name") or inp.get("name")
        if inner:
            return str(inner)
        return name
    if name.startswith("mcp__"):
        return name.rsplit("__", 1)[-1]
    return name


def _shell_command_from_input(inp: Any) -> str:
    if isinstance(inp, str):
        return inp
    if isinstance(inp, dict):
        for key in ("command", "cmd", "script"):
            val = inp.get(key)
            if isinstance(val, str):
                return val
    return ""


def classify_tool(name: str | None, inp: Any = None) -> Bucket:
    """Classify a structured tool call. Unknown tools default to context."""
    canonical = _canonical_tool_name(name, inp)
    if not canonical:
        return "context"
    if canonical in SHELL_TOOLS or (name or "") in SHELL_TOOLS:
        return classify_shell_command(_shell_command_from_input(inp))
    if canonical in CONTEXT_TOOLS:
        return "context"
    if canonical in CODE_TOOLS:
        return "code"
    if canonical in PLANNING_TOOLS:
        return "planning"
    if canonical in OTHER_TOOLS:
        return "other"
    return "context"


def _payload_text(name: str | None, inp: Any) -> str:
    try:
        return json.dumps({"name": name, "input": inp}, ensure_ascii=False, default=str)
    except TypeError:
        return str(inp)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, default=str)
    except TypeError:
        return str(content)


def _add(buckets: dict[str, int], bucket: str, n: int) -> None:
    buckets[bucket] = buckets.get(bucket, 0) + n


def _tool_id_from_block(block: dict[str, Any]) -> str | None:
    for key in ("id", "tool_use_id", "toolUseId", "call_id"):
        val = block.get(key)
        if val:
            return str(val)
    return None


def _accumulate_blocks(
    content: Any,
    buckets: dict[str, int],
    tool_map: ToolBucketMap,
) -> None:
    if isinstance(content, str):
        _add(buckets, "other", len(content))
        return
    if not isinstance(content, list):
        return
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype in {"text", "input_text", "output_text"}:
            _add(buckets, "other", len(block.get("text") or ""))
        elif btype in {"thinking", "reasoning"}:
            _add(buckets, "planning", len(block.get("text") or block.get("thinking") or ""))
        elif btype == "tool_use":
            name = block.get("name")
            inp = block.get("input")
            bucket = classify_tool(name, inp)
            tool_map.record(_tool_id_from_block(block), bucket)
            _add(buckets, bucket, len(_payload_text(name, inp)))
        elif btype == "tool_result":
            bucket = tool_map.lookup(block.get("tool_use_id") or block.get("toolUseId"))
            _add(buckets, bucket, len(_result_text(block.get("content"))))
        elif btype in {"web_search_call", "web_search"}:
            tool_map.record(_tool_id_from_block(block), "context")
            _add(buckets, "context", len(_result_text(block)))


def _accumulate_codex(
    obj: dict[str, Any],
    buckets: dict[str, int],
    tool_map: ToolBucketMap,
) -> None:
    etype = obj.get("type")
    payload = obj.get("payload") or {}
    if etype == "session_meta":
        return
    if etype == "event_msg":
        msg = payload.get("message")
        if isinstance(msg, str) and msg:
            _add(buckets, "other", len(msg))
        return
    if etype != "response_item":
        return
    ptype = payload.get("type")
    if ptype == "message":
        content = payload.get("content") or []
        if isinstance(content, str):
            _add(buckets, "other", len(content))
            return
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            text = block.get("text") or ""
            if text:
                _add(buckets, "other", len(text))
        return
    if ptype == "function_call":
        name = payload.get("name")
        raw_args = payload.get("arguments")
        try:
            inp = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            inp = {"raw": raw_args}
        bucket = classify_tool(name, inp)
        tool_map.record(payload.get("call_id") or payload.get("id"), bucket)
        _add(
            buckets,
            bucket,
            len(
                json.dumps(
                    {"name": name, "arguments": inp},
                    ensure_ascii=False,
                    default=str,
                )
            ),
        )
        return
    if ptype == "function_call_output":
        out = payload.get("output") or ""
        if not isinstance(out, str):
            out = _result_text(out)
        bucket = tool_map.lookup(payload.get("call_id") or payload.get("id"))
        _add(buckets, bucket, len(out))
        return
    if ptype == "web_search_call":
        tool_map.record(payload.get("id") or payload.get("call_id"), "context")
        _add(buckets, "context", len(_result_text(payload)))


def accumulate_jsonl_obj(
    obj: dict[str, Any],
    buckets: dict[str, int],
    tool_map: ToolBucketMap | None = None,
) -> None:
    """Attribute chars from one Cursor / Claude / Codex JSONL object."""
    if tool_map is None:
        tool_map = ToolBucketMap()
    etype = obj.get("type")
    if etype in {"session_meta", "event_msg", "response_item"}:
        _accumulate_codex(obj, buckets, tool_map)
        return
    if etype in {"user", "assistant", "system"} and "message" in obj:
        _accumulate_blocks((obj.get("message") or {}).get("content"), buckets, tool_map)
        return
    if "role" in obj and "message" in obj:
        _accumulate_blocks((obj.get("message") or {}).get("content"), buckets, tool_map)
        return


def _tokens_for_jsonl_lines(
    path: Path, lines: set[int], bucket_names: tuple[str, ...]
) -> int:
    """Two-pass chars/4 of selected buckets. No cap.

    First pass walks the whole file so tool_use_id → bucket pairing works even
    when the tool_use line is outside the selected stretch.
    """
    if not lines:
        return 0
    tool_map = ToolBucketMap()
    parsed: list[tuple[int, dict[str, Any] | None]] = []
    with path.open(encoding="utf-8") as f:
        for i, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                parsed.append((i, None))
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                parsed.append((i, None))
                continue
            if isinstance(obj, dict):
                accumulate_jsonl_obj(obj, empty_buckets(), tool_map)
                parsed.append((i, obj))
            else:
                parsed.append((i, None))

    chars = 0
    for i, obj in parsed:
        if i not in lines or obj is None:
            continue
        buckets = empty_buckets()
        accumulate_jsonl_obj(obj, buckets, tool_map)
        chars += sum(buckets[name] for name in bucket_names)
    if not chars:
        return 0
    return int(round(chars / CHARS_PER_TOKEN))


def context_tokens_for_jsonl_lines(path: Path, lines: set[int]) -> int:
    """Decant Context chars/4 of selected 1-based JSONL lines. No cap.

    Retrieval-only helper (Read/Grep/web/MCP/read-only shell + results).
    Write, user dumps, assistant prose, planning tools, and mutating shell
    contribute 0.
    """
    return _tokens_for_jsonl_lines(path, lines, ("context",))


def learning_tokens_for_jsonl_lines(path: Path, lines: set[int]) -> int:
    """Cost to learn: chars/4 of context + planning + other. Code is 0. No cap.

    Same two-pass as context_tokens_for_jsonl_lines. Includes the question,
    retrieval, thinking, and the conclusion. Write/Edit/mutating shell do not
    count — a note cannot save implementation tokens.
    """
    return _tokens_for_jsonl_lines(path, lines, ("context", "planning", "other"))
