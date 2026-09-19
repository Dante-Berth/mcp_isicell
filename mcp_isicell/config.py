"""Where ISiCell lives on this host.

Paths mirror web/CellBuilder/backend/src/service/isiCellPath.js so the server and
the platform agree on the layout. ISICELL_ROOT is the checkout root, the
directory holding config/, core/, scripts/ and web/.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

ENV_ROOT = "ISICELL_ROOT"


@dataclass(frozen=True)
class IsicellPaths:
    root: Path

    @staticmethod
    def from_env() -> "IsicellPaths | None":
        raw = os.environ.get(ENV_ROOT)
        return IsicellPaths(Path(raw).expanduser().resolve()) if raw else None

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def official_modules_json(self) -> Path:
        return self.config / "officialModules.json"

    @property
    def default_json(self) -> Path:
        return self.config / "default.json"

    @property
    def scripts(self) -> Path:
        return self.root / "scripts"

    @property
    def main_generator(self) -> Path:
        return self.scripts / "mainGenerator.py"

    @property
    def script_launcher(self) -> Path:
        return self.scripts / "scriptLauncher.py"

    @property
    def parameters_exploration(self) -> Path:
        return self.scripts / "paramatersExploration.py"

    @property
    def cell_modules(self) -> Path:
        return self.root / "core" / "modules" / "cellModules"

    @property
    def simulations(self) -> Path:
        return self.root / "simulations"

    def build_dir(self, token: str) -> Path:
        return self.simulations / token

    def installed_modules(self) -> set[str]:
        """DirNames whose config.json is actually present.

        A zip export carries the directories but not their contents, so this is
        what distinguishes a usable module from an uninitialised submodule.
        """
        base = self.cell_modules
        if not base.is_dir():
            return set()
        return {d.name for d in base.iterdir() if d.is_dir() and (d / "config.json").is_file()}

    def load_official_modules(self) -> dict:
        return json.loads(self.official_modules_json.read_text())

    def exists(self) -> bool:
        return self.main_generator.is_file()
