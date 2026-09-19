"""The validator's output vocabulary.

Codes are stable identifiers. Cite them (`E_TRANSITION_UNDEFINED_TARGET`), do not
restate their reasoning -- restatements drift independently from the rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Finding:
    severity: Severity
    code: str
    path: str
    message: str
    suggestion: str = ""

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"[{self.severity}] {self.code} at {self.path}: {self.message}"


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True when nothing would break codegen or the C++ build."""
        return not self.errors

    def codes(self) -> list[str]:
        return [f.code for f in self.findings]
