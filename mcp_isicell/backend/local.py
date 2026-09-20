"""Driving ISiCell as a local process.

The web UI is not privileged: the whole generate-compile pipeline is
`mainGenerator.py <token> <type>` reading builderData JSON on stdin. Running it
directly skips the HTTP API, its cookie session and its IP pinning entirely.

All the I/O in this package lives here. Parsing lives in streams.py, so the
interesting logic stays testable without a C++ toolchain.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from random import randrange

from ..config import IsicellPaths, isicell_python
from ..domain.models import BuilderData
from ..errors import BuildFailed, ConfirmationRequired, NotConfigured
from .simscript import RunSpec, render
from .streams import (
    Progress,
    locate_by_case_label,
    locate_in_model,
    parse_meta_info,
    parse_progress,
)

BUILD_TYPES = ("python", "sql", "all", "debug")

# web/CellBuilder/backend/src/service/safePathCheck.js validateToken. Mirror it
# so a bad token is refused here rather than half-way through a build.
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# A g++/clang diagnostic: path:line:col: severity: message
DIAGNOSTIC = re.compile(r"^(?P<file>[^:\s]+):(?P<line>\d+):(?:(?P<col>\d+):)?\s*(?P<sev>error|warning):\s*(?P<msg>.*)$")


def generate_token() -> str:
    """A fresh build token, shaped like the ones compile.js mints."""
    now = datetime.now()
    return f"sim_{now.year}{now.month}{now.day}_{now.hour}{now.minute}_{randrange(1000)}"


def validate_token(token: str) -> str:
    if not TOKEN_RE.match(token or ""):
        raise ValueError(
            f"invalid token {token!r}: ISiCell accepts letters, digits, underscore and hyphen only"
        )
    return token


@dataclass
class BuildResult:
    token: str
    ok: bool
    returncode: int
    phase: str
    percent: int
    stdout_tail: list[str] = field(default_factory=list)
    stderr_tail: list[str] = field(default_factory=list)
    meta_info: dict = field(default_factory=dict)
    diagnostics: list[dict] = field(default_factory=list)
    build_dir: str | None = None


@dataclass
class LocalBackend:
    paths: IsicellPaths
    python: str = field(default_factory=isicell_python)
    timeout: int = 1800

    def require(self) -> None:
        if not self.paths.exists():
            raise NotConfigured(
                f"No mainGenerator.py under {self.paths.root}. Set ISICELL_ROOT to an "
                "ISiCell checkout to compile and run; authoring and validation work without it."
            )

    def compile(
        self,
        model: BuilderData,
        build_type: str = "python",
        token: str | None = None,
        confirm: bool = False,
    ) -> BuildResult:
        """Generate the C++ tree and build it.

        Reusing a token is destructive: mainGenerator.py rmtree's the simulation
        directory before regenerating, so it requires confirm=True.
        """
        self.require()
        if build_type not in BUILD_TYPES:
            raise ValueError(f"build_type must be one of {BUILD_TYPES}, got {build_type!r}")

        if token is None:
            token = generate_token()
        else:
            validate_token(token)
            if self.paths.build_dir(token).exists() and not confirm:
                raise ConfirmationRequired(
                    f"Build {token!r} already exists and compiling would delete it "
                    "(mainGenerator.py removes the directory before regenerating). "
                    "Pass confirm=True, or omit the token for a fresh one."
                )

        payload = json.dumps(model.to_generator_json())
        proc = subprocess.run(
            [self.python, str(self.paths.main_generator), token, build_type],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(self.paths.root),
            timeout=self.timeout,
        )
        progress = parse_progress([proc.stdout])
        meta = parse_meta_info(proc.stderr)
        return BuildResult(
            token=token,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            phase=progress.phase,
            percent=progress.percent,
            stdout_tail=progress.lines[-40:],
            stderr_tail=(proc.stderr or "").splitlines()[-40:],
            meta_info=meta,
            diagnostics=self._diagnostics(proc.stdout, proc.stderr, meta, token),
            build_dir=str(self.paths.build_dir(token)),
        )

    def _diagnostics(self, stdout: str, stderr: str, meta: dict, token: str) -> list[dict]:
        """Compiler errors, mapped back onto model elements where possible."""
        out: list[dict] = []
        sources: dict[str, str | None] = {}
        for line in (stdout or "").splitlines() + (stderr or "").splitlines():
            m = DIAGNOSTIC.match(line.strip())
            if not m:
                continue
            filename = Path(m.group("file")).name
            entry = {
                "file": filename,
                "line": int(m.group("line")),
                "severity": m.group("sev"),
                "message": m.group("msg"),
            }
            owner = locate_in_model(meta, filename, entry["line"])
            if owner:
                entry["model_element"] = owner.get("element")
                entry["idFunc"] = owner.get("idFunc")
            else:
                # No marker: Isigen emits them for functions, codeInit and
                # codeLoop only, so state code and transition conditions land
                # here. The generated switch still names them.
                if filename not in sources:
                    sources[filename] = self._generated_source(token, filename)
                source = sources[filename]
                fallback = locate_by_case_label(source, entry["line"]) if source else None
                if fallback:
                    entry["model_element"] = fallback["element"]
                    entry["kind"] = fallback["kind"]
                    entry["model_path"] = fallback["model_path"]
                    entry["attributed_by"] = fallback["attributed_by"]
            out.append(entry)
        return out

    def _generated_source(self, token: str, filename: str) -> str | None:
        """The generated file a diagnostic points at, if it is still on disk.

        Read rather than kept in memory: the tree is rewritten on every build, so
        the copy that produced this error is the one beside the build.
        """
        if "/" in filename or "\\" in filename:
            return None
        root = self.paths.build_dir(token) / "src"
        if not root.is_dir():
            return None
        for candidate in root.rglob(filename):
            try:
                return candidate.read_text(errors="replace")
            except OSError:
                return None
        return None

    def checksum(self, token: str) -> str | None:
        """The md5 of the model JSON this build was made from, if recorded."""
        validate_token(token)
        path = self.paths.build_dir(token) / "checksum.txt"
        return path.read_text().strip() if path.is_file() else None

    def list_builds(self) -> list[dict]:
        base = self.paths.simulations
        if not base.is_dir():
            return []
        out = []
        for d in sorted(base.iterdir()):
            if not d.is_dir():
                continue
            out.append(
                {
                    "token": d.name,
                    "compiled": (d / "build" / "console").is_file(),
                    "python": (d / "isicell" / "_isiCellPy.so").is_file(),
                }
            )
        return out

    def run_script(self, token: str, script: Path, cwd: Path | None = None) -> dict:
        """Run a Python script against a built model via scriptLauncher.py.

        scriptLauncher puts simulations/<token> on sys.path, so `from isicell
        import *` resolves to that build.
        """
        self.require()
        validate_token(token)
        build = self.paths.build_dir(token)
        if not build.is_dir():
            raise BuildFailed(f"no such build: {token}")
        try:
            proc = subprocess.run(
                [self.python, "-u", str(self.paths.script_launcher), str(build), str(script)],
                capture_output=True,
                text=True,
                cwd=str(cwd or script.parent),
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            # Raised bare this reaches the client as "Error executing tool",
            # throwing away the one thing the caller needs: which path is wrong.
            raise BuildFailed(
                f"the run interpreter {self.python!r} does not exist. Set "
                "ISICELL_PYTHON to the interpreter the build was compiled against."
            ) from exc
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    def simulate(self, token: str, spec: RunSpec) -> dict:
        """Run a generated script against a build and return its measurements.

        The script is written into the build directory rather than the user's
        workspace, so a run leaves nothing behind where a person keeps files.
        """
        self.require()
        validate_token(token)
        build = self.paths.build_dir(token)
        if not (build / "isicell" / "__init__.py").is_file():
            raise BuildFailed(
                f"build {token!r} has no Python module. Compile with type='python' or 'all'."
            )
        script = build / "_mcp_run.py"
        script.write_text(render(spec))
        try:
            result = self.run_script(token, script, cwd=build)
        finally:
            script.unlink(missing_ok=True)
        return {**result, "measurements": _last_json_object(result.get("stdout", ""))}

    def remove_build(self, token: str, confirm: bool = False) -> dict:
        validate_token(token)
        if not confirm:
            raise ConfirmationRequired(f"Removing build {token!r} is irreversible.")
        target = self.paths.build_dir(token)
        if not target.is_dir():
            return {"removed": False, "reason": "no such build"}
        shutil.rmtree(target)
        return {"removed": True, "token": token}


def _last_json_object(stdout: str) -> dict | None:
    """The result line the generated script prints.

    Model code writes to stdout too, so the payload is the last line that parses
    as a JSON object, not the first and not the whole stream.
    """
    for line in reversed((stdout or "").splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def backend_from_env() -> LocalBackend | None:
    paths = IsicellPaths.from_env()
    return LocalBackend(paths, python=isicell_python()) if paths else None


__all__ = [
    "BUILD_TYPES",
    "BuildResult",
    "RunSpec",
    "LocalBackend",
    "Progress",
    "backend_from_env",
    "generate_token",
    "validate_token",
]
