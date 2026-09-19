"""Official module catalogue and dependency resolution.

Dependencies in config/officialModules.json come in two kinds and both must be
satisfied:

  - another module's `Type`, e.g. Grimes Oxygen depends on "Spheroid Manager"
  - a dimensionality pseudo-capability, "2D" or "3D", which no module declares
    as its Type but which a module's own Type implies ("2D Physics" -> 2D)

Treating them uniformly is what makes the checker correct for both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SPACE_2D = "2D"
SPACE_3D = "3D"


@dataclass(frozen=True)
class Module:
    Name: str
    DirName: str
    Description: str = ""
    Type: str = ""
    Dependencies: tuple[str, ...] = ()
    Link: str = ""

    def space(self) -> str | None:
        """The dimensionality this module implies, from its Type or deps."""
        haystack = (self.Type, *self.Dependencies)
        for token in haystack:
            if token == SPACE_2D or token.startswith("2D"):
                return SPACE_2D
            if token == SPACE_3D or token.startswith("3D"):
                return SPACE_3D
        return None


@dataclass(frozen=True)
class Catalogue:
    modules: tuple[Module, ...]

    @staticmethod
    def from_json(payload: dict) -> "Catalogue":
        return Catalogue(
            tuple(
                Module(
                    Name=m["Name"],
                    DirName=m["DirName"],
                    Description=m.get("Description", ""),
                    Type=m.get("Type", ""),
                    Dependencies=tuple(m.get("Dependencies", ())),
                    Link=m.get("Link", ""),
                )
                for m in payload.get("Modules", [])
            )
        )

    @staticmethod
    def from_path(path: str | Path) -> "Catalogue":
        return Catalogue.from_json(json.loads(Path(path).read_text()))

    def by_name(self, name: str) -> Module | None:
        for m in self.modules:
            if m.Name == name:
                return m
        return None

    def names(self) -> list[str]:
        return [m.Name for m in self.modules]

    def capabilities(self, selected: list[str]) -> set[str]:
        """Every capability the selected set provides: Types plus dimensions."""
        caps: set[str] = set()
        for name in selected:
            mod = self.by_name(name)
            if mod is None:
                continue
            if mod.Type:
                caps.add(mod.Type)
            space = mod.space()
            if space:
                caps.add(space)
        return caps

    def unmet(self, selected: list[str]) -> list[tuple[str, str]]:
        """(module Name, missing capability) for each unsatisfied dependency."""
        caps = self.capabilities(selected)
        missing: list[tuple[str, str]] = []
        for name in selected:
            mod = self.by_name(name)
            if mod is None:
                continue
            for dep in mod.Dependencies:
                if dep not in caps:
                    missing.append((name, dep))
        return missing

    def spaces(self, selected: list[str]) -> set[str]:
        out: set[str] = set()
        for name in selected:
            mod = self.by_name(name)
            if mod is None:
                continue
            space = mod.space()
            if space:
                out.add(space)
        return out
