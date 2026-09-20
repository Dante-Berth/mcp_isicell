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
ENV_JOBS = "ISICELL_JOBS_DIR"
ENV_STATE = "ISICELL_STATE_DIR"
ENV_PYTHON = "ISICELL_PYTHON"


def isicell_python() -> str:
    """The interpreter that runs generated simulation scripts.

    A build's `_isiCellPy.so` is compiled against one specific interpreter, and
    the scripts import isicellpy's own dependencies (pandas, tqdm, networkx,
    plotly, seaborn, ...). The server's own interpreter usually has neither, so
    this has to be settable apart from however the server was started.
    """
    return os.environ.get(ENV_PYTHON) or "python3"


def state_directory() -> Path:
    """Where the server keeps what must survive a restart."""
    raw = os.environ.get(ENV_STATE)
    if raw:
        return Path(raw).expanduser()
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base).expanduser() / "mcp_isicell"


def session_path() -> Path:
    """The autosaved authoring session."""
    return state_directory() / "session.json"


def jobs_directory() -> Path:
    """Where background jobs keep their registry and logs.

    Independent of ISICELL_ROOT: a job outlives the call that started it, so its
    log must not live inside a simulation directory that a later compile with
    the same token would delete.
    """
    raw = os.environ.get(ENV_JOBS)
    if raw:
        return Path(raw).expanduser()
    return state_directory() / "jobs"


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
