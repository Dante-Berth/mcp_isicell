"""Runs that outlive a tool call.

A twelve-run sweep blocks a tool call for as long as twelve simulations take.
This starts one detached, records it in a JSON registry, and reconciles its
status on read -- the shape ISiCell itself uses for background runs
(web/CellBuilder/backend/src/routes/python.js:333).

Three departures from that implementation, because this registry is read by a
long-lived server rather than by a request handler that exits:

  - a PID is not proof of identity. PIDs are reused, so a finished job whose
    number was recycled would otherwise be reported as still running. The
    process start time is recorded alongside, and both must match.
  - a finished job is a zombie until someone waits on it. These processes are
    the server's own children, so `/proc/<pid>` outlives them and a liveness
    check on the directory alone reports every completed job as running
    forever. Reconciliation reaps them and keeps the exit status.
  - the registry is rewritten atomically, because a status reconciliation can
    race a job starting.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import IsicellError

RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
INTERRUPTED = "interrupted"

REGISTRY = "jobs.json"
TAIL_LINES = 200


class JobError(IsicellError):
    """A job could not be started, found, or stopped."""


def _stat_fields(pid: int) -> list[str] | None:
    """The fields of /proc/<pid>/stat after the parenthesised comm."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            data = fh.read().decode("utf-8", "replace")
    except OSError:
        return None
    # comm is parenthesised and may itself contain spaces and brackets.
    close = data.rfind(")")
    if close == -1:
        return None
    return data[close + 2 :].split()


def _proc_start_time(pid: int) -> str | None:
    """Start time in clock ticks since boot (field 22 of /proc/<pid>/stat).

    Together with the PID this identifies one process run. Without it a
    recycled PID looks exactly like the original job still running.
    """
    fields = _stat_fields(pid)
    return fields[19] if fields and len(fields) > 19 else None


def _is_zombie(pid: int) -> bool:
    """A process that has exited but has not been waited on.

    Background jobs are this server's own children, so they stay in the process
    table -- and /proc -- until reaped. Treating a zombie as alive would report
    every finished job as running forever.
    """
    fields = _stat_fields(pid)
    return bool(fields) and fields[0] == "Z"


def _alive(pid: int, started: str | None) -> bool:
    if pid <= 0:
        return False
    current = _proc_start_time(pid)
    if current is None:
        return False
    # A recorded start time that no longer matches means the PID was reused.
    if started is not None and current != started:
        return False
    return not _is_zombie(pid)


def _reap(pid: int) -> int | None:
    """Wait on a finished child without blocking. Returns its exit code."""
    try:
        done, status = os.waitpid(pid, os.WNOHANG)
    except (ChildProcessError, OSError):
        return None
    if done != pid:
        return None
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    if os.WIFSIGNALED(status):
        return -os.WTERMSIG(status)
    return None


@dataclass
class JobStore:
    """The registry of background jobs, under a directory the server owns."""

    directory: Path
    _registry: Path = field(init=False)

    def __post_init__(self) -> None:
        self.directory = Path(self.directory).expanduser()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._registry = self.directory / REGISTRY

    def _read(self) -> dict[str, Any]:
        if not self._registry.is_file():
            return {}
        try:
            return json.loads(self._registry.read_text())
        except json.JSONDecodeError:
            return {}

    def _write(self, jobs: dict[str, Any]) -> None:
        """Replace the registry atomically, so a concurrent read never sees half."""
        temporary = self._registry.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(jobs, indent=2))
        temporary.replace(self._registry)

    def log_path(self, token: str) -> Path:
        return self.directory / f"{token}.log"

    def start(self, token: str, command: list[str], cwd: Path, label: str = "") -> dict[str, Any]:
        """Launch a detached process, its output going to the job's log."""
        jobs = self._read()
        if token in jobs and jobs[token].get("status") == RUNNING:
            raise JobError(f"job {token!r} is already running")
        log = self.log_path(token)
        handle = log.open("wb")
        try:
            process = subprocess.Popen(
                command,
                cwd=str(cwd),
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                # Its own process group, so stopping the job does not signal
                # the server that started it.
                start_new_session=True,
            )
        finally:
            handle.close()
        entry = {
            "token": token,
            "pid": process.pid,
            "started_at": time.time(),
            "proc_start": _proc_start_time(process.pid),
            "status": RUNNING,
            "label": label,
            "log": str(log),
            "command": command,
        }
        jobs[token] = entry
        self._write(jobs)
        return entry

    def _reconcile(self, jobs: dict[str, Any]) -> bool:
        """Settle every job whose process has ended. True if anything changed.

        A job that exited non-zero is `failed`, not `finished` -- the difference
        is the whole reason to look at a job you did not watch.
        """
        changed = False
        for entry in jobs.values():
            if entry.get("status") != RUNNING:
                continue
            pid = int(entry.get("pid", -1))
            if _alive(pid, entry.get("proc_start")):
                continue
            code = _reap(pid)
            if code is not None:
                entry["exit_code"] = code
            entry["status"] = FAILED if (code not in (0, None)) else FINISHED
            entry["ended_at"] = time.time()
            changed = True
        return changed

    def list(self) -> list[dict[str, Any]]:
        jobs = self._read()
        if self._reconcile(jobs):
            self._write(jobs)
        return sorted(jobs.values(), key=lambda e: e.get("started_at", 0), reverse=True)

    def status(self, token: str) -> dict[str, Any]:
        jobs = self._read()
        if token not in jobs:
            raise JobError(f"no such job: {token!r}")
        if self._reconcile(jobs):
            self._write(jobs)
        entry = dict(jobs[token])
        log = Path(entry.get("log", ""))
        entry["log_bytes"] = log.stat().st_size if log.is_file() else 0
        return entry

    def log(self, token: str, tail: int = TAIL_LINES) -> dict[str, Any]:
        entry = self.status(token)
        path = Path(entry.get("log", ""))
        if not path.is_file():
            return {**entry, "lines": []}
        lines = path.read_text(errors="replace").splitlines()
        return {**entry, "lines": lines[-max(1, tail) :], "total_lines": len(lines)}

    def stop(self, token: str, confirm: bool = False) -> dict[str, Any]:
        if not confirm:
            raise JobError(f"stopping job {token!r} kills a running simulation")
        jobs = self._read()
        if token not in jobs:
            raise JobError(f"no such job: {token!r}")
        entry = jobs[token]
        pid = int(entry.get("pid", -1))
        if entry.get("status") != RUNNING or not _alive(pid, entry.get("proc_start")):
            entry["status"] = entry.get("status", FINISHED)
            self._write(jobs)
            return {**entry, "stopped": False, "reason": "not running"}
        # Signal the group, so a launcher's children go too.
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError) as exc:
            raise JobError(f"cannot stop job {token!r}: {exc}") from exc
        entry["status"] = INTERRUPTED
        entry["ended_at"] = time.time()
        # Reap it so the PID is released rather than left as a zombie.
        for _ in range(50):
            if _reap(pid) is not None or not _stat_fields(pid):
                break
            time.sleep(0.01)
        self._write(jobs)
        return {**entry, "stopped": True}

    def forget(self, token: str, confirm: bool = False) -> dict[str, Any]:
        """Drop a finished job and its log."""
        jobs = self._read()
        if token not in jobs:
            raise JobError(f"no such job: {token!r}")
        self._reconcile(jobs)
        if jobs[token].get("status") == RUNNING:
            raise JobError(f"job {token!r} is still running; stop it first")
        if not confirm:
            raise JobError(f"forgetting job {token!r} deletes its log")
        log = Path(jobs[token].get("log", ""))
        if log.is_file():
            log.unlink()
        removed = jobs.pop(token)
        self._write(jobs)
        return {"forgotten": True, "token": token, "status": removed.get("status")}
