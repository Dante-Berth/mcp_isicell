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


# The switch cases Isigen generates for state behaviour and transitions. Both
# `behave()` and `nextState()` build their bodies from the same caseTemplate
# (PrimoCell.hpp:18), so a case label is the one structural anchor that survives
# into the generated C++.
CASE_LABEL = re.compile(r"^\s*case\s+State::(?P<name>\w+)\s*:")
# The two generated switch statements, named by the member function holding them.
SWITCH_OWNER = re.compile(r"^\s*void\s+(?P<fn>behave|nextState)\s*\(\s*\)")

# What each generated function was built from, in the model's own vocabulary.
OWNER_KIND = {
    "behave": ("state", "cell.States[{name}].code"),
    "nextState": ("transition", "cell.Transitions[from={name}].condition"),
}


def locate_by_case_label(source: str, line: int) -> dict | None:
    """Find the state or transition owning a generated line, without metaInfo.

    Isigen emits `//isigen:metaDataDebug` markers for functions, codeInit and
    codeLoop only (PrimoCell.hpp:182, Scenario.hpp:39), so a compiler error in a
    state's behaviour or a transition's condition maps to nothing -- and that is
    the likeliest place for hand-written C++ to be wrong. The generated shape is
    recoverable anyway: `behave()` and `nextState()` are switches of
    `case State::<name>` blocks, so the nearest case label at or above the error
    names the element, and the enclosing function says which half of the model it
    came from.

    Returns None rather than guessing when the line is outside both switches.
    """
    owner_fn: str | None = None
    case_name: str | None = None
    case_line: int | None = None
    depth_marker: str | None = None

    for number, text in enumerate(source.splitlines(), start=1):
        if number > line:
            break
        switch = SWITCH_OWNER.match(text)
        if switch:
            # A new generated function begins; anything before it is not ours.
            owner_fn = switch.group("fn")
            case_name = case_line = None
            depth_marker = None
            continue
        label = CASE_LABEL.match(text)
        if label and owner_fn:
            case_name = label.group("name")
            case_line = number
        elif text.strip() == "default:" and owner_fn:
            # Past the last generated case: the rest of the switch is template
            # boilerplate, so an error there belongs to no model element.
            depth_marker = "default"

    if not owner_fn or not case_name or depth_marker == "default":
        return None

    kind, path = OWNER_KIND[owner_fn]
    return {
        "element": case_name,
        "kind": kind,
        "model_path": path.format(name=case_name),
        "line": case_line,
        "attributed_by": "case-label",
    }
