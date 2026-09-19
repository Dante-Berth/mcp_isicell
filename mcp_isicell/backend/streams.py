"""Pure parsing of what the generator prints.

mainGenerator.py runs cmake and make under a pty and forwards their output
verbatim, so progress arrives as arbitrarily-chunked bytes rather than tidy
lines. Both parsers here take an iterable of chunks and are total: they never
raise on malformed input, because their whole job is to make sense of output
that failed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

# make(1) prefixes each recipe line with its completion percentage.
MAKE_PERCENT = re.compile(r"^\[\s*(\d{1,3})%\]")
# cmake's own progress lines start with "-- ".
CMAKE_LINE = re.compile(r"^-- ")
BUILD_FILES_WRITTEN = re.compile(r"Build files have been written to:", re.IGNORECASE)
ANSI = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
# Isigen prints this to stderr: a map from generated C++ lines back to the model.
META_INFO = re.compile(r"^metaInfo:\s*(\{.*\})\s*$", re.MULTILINE)


@dataclass
class Progress:
    phase: str = "preparing"
    percent: int = 0
    lines: list[str] = field(default_factory=list)


def iter_lines(chunks: Iterable[str]) -> Iterator[str]:
    """Reassemble lines across chunk boundaries, ANSI stripped.

    A chunk may split a line anywhere, including mid-escape-sequence, so the tail
    is carried until a newline actually arrives.
    """
    buffer = ""
    for chunk in chunks:
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            yield ANSI.sub("", line.rstrip("\r"))
    if buffer:
        yield ANSI.sub("", buffer.rstrip("\r"))


def parse_progress(chunks: Iterable[str]) -> Progress:
    """Track build phase and the highest percentage seen."""
    progress = Progress()
    for line in iter_lines(chunks):
        progress.lines.append(line)
        if CMAKE_LINE.match(line) and progress.phase == "preparing":
            progress.phase = "configuring"
        if BUILD_FILES_WRITTEN.search(line):
            progress.phase = "building"
        m = MAKE_PERCENT.match(line)
        if m:
            progress.phase = "building"
            # make can report out of order under -j; keep the furthest point.
            progress.percent = max(progress.percent, min(100, int(m.group(1))))
    return progress


def parse_meta_info(stderr: str) -> dict:
    """The generated-line-to-model map Isigen prints, or {} if absent.

    This is what lets a g++ error at PrimoCell.hpp:212 be reported as the cell
    function the user actually wrote, rather than as a line in a generated file
    that is rewritten on every build.
    """
    match = META_INFO.search(stderr or "")
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def locate_in_model(meta: dict, filename: str, line: int) -> dict | None:
    """Find the model element owning a generated line, if any.

    Entries look like {"<file>": {"<key>": {"line": N, "column": N, ...}}}. The
    owner is the entry with the greatest line not after the one reported.
    """
    entries = meta.get(filename) or {}
    best_key, best = None, None
    for key, info in entries.items():
        start = info.get("line")
        if not isinstance(start, int) or start > line:
            continue
        if best is None or start > best.get("line", -1):
            best_key, best = key, info
    if best is None:
        return None
    return {"element": best_key, **best}
