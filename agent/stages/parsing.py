"""Turning model output and workload files into structured values.

Three parsing jobs the code-generating stages all need, each easy to get subtly
wrong:

- **Fenced code.** Models wrap output in ``` fences whatever the instructions
  say, and a fence written into a ``.hpp`` produces a diagnostic pointing at the
  fence rather than at the real problem — which then sends the fix loop chasing
  the wrong line.
- **JSON with prose around it.** Same story for a JSON output field: the object
  is there, with an apology in front of it.
- **The query workload.** One ``.sql`` file holds the whole workload, and the
  stages need a stable id per query to key sources, gold files and report rows
  on. Splitting on ``;`` with a plain ``str.split`` would break on a semicolon
  inside a string literal or a comment, so the splitter is a real scanner.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

from agent.errors import AgentError

# "-- Q3: top customers" / "--q12" / "-- query 4". The `q`/`query` is required:
# matching a bare number would turn "-- filter on 1995 dates" into query q1995.
_ID_PATTERN = re.compile(r"--+\s*(?:query\s+|q)\s*(\d+)\b", re.I)
_FENCE_PATTERN = re.compile(r"^\s*```[^\n]*\n(?P<body>.*?)\n\s*```\s*$", re.S)


class ParseError(AgentError):
    """Model output could not be parsed into the shape the stage requires."""


def extract_code(text: str) -> str:
    """Return source code from a model's output field, minus any fence.

    Handles both a whole fenced block and a fence that only wraps part of the
    output (a model that adds a sentence before the code). Text with no fence is
    returned as-is, so this is safe to call unconditionally.
    """
    if not text:
        return ""
    stripped = text.strip()
    match = _FENCE_PATTERN.match(stripped)
    if match:
        return match.group("body").strip() + "\n"
    if "```" in stripped:
        # A fence somewhere in the middle: keep the largest fenced region.
        parts = stripped.split("```")
        # parts[1::2] are the fenced bodies; the first line of each may be a
        # language tag rather than code.
        bodies = []
        for body in parts[1::2]:
            lines = body.splitlines()
            if lines and lines[0].strip().isalpha() and len(lines[0].strip()) <= 12:
                lines = lines[1:]
            bodies.append("\n".join(lines).strip())
        best = max(bodies, key=len, default="")
        if best:
            return best + "\n"
    return stripped + "\n"


def extract_json(text: str, what: str = "output") -> Any:
    """Parse a JSON value out of a model output field.

    Tolerates a fence and surrounding prose by scanning for the outermost
    balanced ``{...}`` or ``[...]``, with string literals respected so a brace
    inside a value cannot end the scan early.

    Raises:
        ParseError: naming what failed and showing the head of the output, since
            the alternative is a ``JSONDecodeError`` with no context about which
            stage or field produced it.
    """
    if not text or not text.strip():
        raise ParseError(f"The model returned an empty {what}.")
    candidate = extract_code(text).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    span = _balanced_span(candidate)
    if span is None:
        raise ParseError(
            f"The model's {what} contains no JSON object or array. "
            f"Output began: {candidate[:200]!r}"
        )
    try:
        return json.loads(candidate[span[0] : span[1]])
    except json.JSONDecodeError as exc:
        raise ParseError(
            f"The model's {what} is not valid JSON ({exc}). "
            f"Extracted: {candidate[span[0] : span[0] + 200]!r}"
        ) from exc


def _balanced_span(text: str) -> Optional[tuple[int, int]]:
    """Index span of the first balanced JSON object or array in ``text``."""
    openers = {"{": "}", "[": "]"}
    start = next((i for i, ch in enumerate(text) if ch in openers), None)
    if start is None:
        return None
    opener = text[start]
    closer = openers[opener]
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return start, index + 1
    return None


class Query(BaseModel):
    """One statement from the workload file."""

    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    # The `-- Q3: revenue by segment` header, when the file has one. Passed to
    # the generator as intent the SQL alone does not state.
    label: str = ""
    index: int = 0

    @property
    def slug(self) -> str:
        """Filesystem-safe form of the id, for source and gold filenames."""
        return re.sub(r"[^A-Za-z0-9_.-]", "_", self.id)


def parse_workload(text: str) -> list[Query]:
    """Split a workload file into queries with stable ids.

    An id comes from a ``-- Q<n>`` header when the file provides one, because
    those ids appear in the user's own notes, gold filenames and report rows;
    otherwise queries are numbered ``q1``, ``q2``, … in file order. Duplicates
    are disambiguated rather than silently collapsed, since two queries sharing
    a gold file is a correctness hazard.
    """
    queries: list[Query] = []
    seen: dict[str, int] = {}
    for position, chunk in enumerate(_split_statements(text), start=1):
        body = chunk.strip()
        if not body or _is_only_comments(body):
            continue
        label = _leading_comment(body)
        match = _ID_PATTERN.search(label) if label else None
        raw_id = f"q{match.group(1)}" if match else f"q{position}"
        count = seen.get(raw_id, 0) + 1
        seen[raw_id] = count
        query_id = raw_id if count == 1 else f"{raw_id}_{count}"
        queries.append(
            Query(
                id=query_id,
                text=body if body.endswith(";") else body + ";",
                label=label,
                index=len(queries) + 1,
            )
        )
    return queries


def _is_only_comments(chunk: str) -> bool:
    """True when a chunk holds nothing but comments and whitespace."""
    for line in chunk.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            return False
    return True


def _leading_comment(chunk: str) -> str:
    """The run of ``--`` comment lines at the top of a chunk, joined."""
    lines: list[str] = []
    for line in chunk.splitlines():
        stripped = line.strip()
        if not stripped:
            if lines:
                break
            continue
        if stripped.startswith("--"):
            lines.append(stripped)
            continue
        break
    return " ".join(lines)


def _split_statements(text: str) -> list[str]:
    """Split SQL on statement-terminating semicolons.

    Scans rather than splits: a ``;`` inside a string literal, a ``--`` comment
    or a ``/* */`` block does not end a statement. A trailing statement with no
    semicolon is kept, because workload files routinely omit the last one.
    """
    out: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(text)
    in_single = in_double = in_line_comment = in_block_comment = False

    while index < length:
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""

        if in_line_comment:
            buffer.append(char)
            if char == "\n":
                in_line_comment = False
            index += 1
            continue
        if in_block_comment:
            buffer.append(char)
            if char == "*" and following == "/":
                buffer.append(following)
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue
        if in_single or in_double:
            buffer.append(char)
            quote = "'" if in_single else '"'
            if char == quote:
                if following == quote:  # doubled quote escapes itself in SQL
                    buffer.append(following)
                    index += 2
                    continue
                in_single = in_double = False
            index += 1
            continue

        if char == "-" and following == "-":
            in_line_comment = True
        elif char == "/" and following == "*":
            in_block_comment = True
        elif char == "'":
            in_single = True
        elif char == '"':
            in_double = True
        elif char == ";":
            out.append("".join(buffer))
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1

    tail = "".join(buffer)
    if tail.strip():
        out.append(tail)
    return out
