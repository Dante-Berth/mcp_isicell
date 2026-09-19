"""Jobs that keep running after the tool call returns."""

import json
import sys
import time

import pytest

from mcp_isicell.backend.jobs import FINISHED, INTERRUPTED, RUNNING, JobError, JobStore


def store(tmp_path) -> JobStore:
    return JobStore(tmp_path / "jobs")


def sleeper(seconds: float) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def printer(text: str) -> list[str]:
    return [sys.executable, "-c", f"print({text!r})"]


def wait_until_finished(s: JobStore, token: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        entry = s.status(token)
        if entry["status"] != RUNNING:
            return entry
        time.sleep(0.05)
    raise AssertionError(f"job {token} never finished")


def test_a_started_job_is_running_and_the_call_returns_at_once(tmp_path):
    s = store(tmp_path)
    began = time.time()
    entry = s.start("j1", sleeper(3), cwd=tmp_path)
    assert entry["status"] == RUNNING
    # The point of a background job: starting it does not wait for it.
    assert time.time() - began < 2.0
    s.stop("j1", confirm=True)


def test_a_finished_job_is_reported_as_finished_without_being_polled(tmp_path):
    s = store(tmp_path)
    s.start("j2", printer("done"), cwd=tmp_path)
    assert wait_until_finished(s, "j2")["status"] == FINISHED


def test_a_jobs_output_is_captured_to_its_log(tmp_path):
    s = store(tmp_path)
    s.start("j3", printer("hello from the job"), cwd=tmp_path)
    wait_until_finished(s, "j3")
    assert "hello from the job" in s.log("j3")["lines"]


def test_a_log_can_be_tailed_rather_than_read_whole(tmp_path):
    s = store(tmp_path)
    s.start(
        "j4",
        [sys.executable, "-c", "[print(i) for i in range(500)]"],
        cwd=tmp_path,
    )
    wait_until_finished(s, "j4")
    out = s.log("j4", tail=10)
    assert out["total_lines"] == 500 and len(out["lines"]) == 10
    assert out["lines"][-1] == "499"


def test_stderr_lands_in_the_same_log_as_stdout(tmp_path):
    """A job that fails should not have its reason in a place nobody reads."""
    s = store(tmp_path)
    s.start(
        "j5",
        [sys.executable, "-c", "import sys; print('bad', file=sys.stderr)"],
        cwd=tmp_path,
    )
    wait_until_finished(s, "j5")
    assert "bad" in s.log("j5")["lines"]


def test_a_running_job_can_be_stopped(tmp_path):
    s = store(tmp_path)
    s.start("j6", sleeper(60), cwd=tmp_path)
    out = s.stop("j6", confirm=True)
    assert out["stopped"] is True
    assert s.status("j6")["status"] == INTERRUPTED


def test_stopping_needs_confirmation_because_it_kills_a_simulation(tmp_path):
    s = store(tmp_path)
    s.start("j7", sleeper(30), cwd=tmp_path)
    with pytest.raises(JobError):
        s.stop("j7")
    assert s.status("j7")["status"] == RUNNING
    s.stop("j7", confirm=True)


def test_stopping_a_job_that_already_ended_is_not_an_error(tmp_path):
    s = store(tmp_path)
    s.start("j8", printer("x"), cwd=tmp_path)
    wait_until_finished(s, "j8")
    assert s.stop("j8", confirm=True)["stopped"] is False


def test_a_recycled_pid_does_not_resurrect_a_finished_job(tmp_path):
    """A PID alone is not identity; without the start time this reads as running."""
    s = store(tmp_path)
    s.start("j9", printer("x"), cwd=tmp_path)
    wait_until_finished(s, "j9")

    # Pretend the PID was reused by a live process: our own.
    registry = tmp_path / "jobs" / "jobs.json"
    jobs = json.loads(registry.read_text())
    jobs["j9"]["status"] = RUNNING
    jobs["j9"]["pid"] = __import__("os").getpid()
    registry.write_text(json.dumps(jobs))

    assert s.status("j9")["status"] == FINISHED


def test_a_live_process_with_a_matching_start_time_is_still_running(tmp_path):
    """The other side of the same check: a real job is not falsely reaped."""
    s = store(tmp_path)
    s.start("j10", sleeper(30), cwd=tmp_path)
    assert s.status("j10")["status"] == RUNNING
    s.stop("j10", confirm=True)


def test_two_jobs_with_the_same_token_cannot_run_at_once(tmp_path):
    s = store(tmp_path)
    s.start("dup", sleeper(30), cwd=tmp_path)
    with pytest.raises(JobError):
        s.start("dup", sleeper(30), cwd=tmp_path)
    s.stop("dup", confirm=True)


def test_a_token_can_be_reused_once_its_job_has_ended(tmp_path):
    s = store(tmp_path)
    s.start("again", printer("first"), cwd=tmp_path)
    wait_until_finished(s, "again")
    s.start("again", printer("second"), cwd=tmp_path)
    wait_until_finished(s, "again")
    assert "second" in s.log("again")["lines"]


def test_jobs_are_listed_newest_first(tmp_path):
    s = store(tmp_path)
    s.start("a", printer("1"), cwd=tmp_path)
    time.sleep(0.02)
    s.start("b", printer("2"), cwd=tmp_path)
    assert [e["token"] for e in s.list()] == ["b", "a"]


def test_an_unknown_job_is_refused_by_name(tmp_path):
    with pytest.raises(JobError) as excinfo:
        store(tmp_path).status("ghost")
    assert "ghost" in str(excinfo.value)


def test_a_running_job_cannot_be_forgotten(tmp_path):
    s = store(tmp_path)
    s.start("keep", sleeper(30), cwd=tmp_path)
    with pytest.raises(JobError) as excinfo:
        s.forget("keep", confirm=True)
    assert "still running" in str(excinfo.value)
    s.stop("keep", confirm=True)


def test_forgetting_a_finished_job_removes_it_and_its_log(tmp_path):
    s = store(tmp_path)
    s.start("gone", printer("x"), cwd=tmp_path)
    wait_until_finished(s, "gone")
    log = s.log_path("gone")
    assert log.is_file()
    s.forget("gone", confirm=True)
    assert not log.is_file()
    assert [e["token"] for e in s.list()] == []


def test_forgetting_needs_confirmation_because_it_deletes_the_log(tmp_path):
    s = store(tmp_path)
    s.start("hold", printer("x"), cwd=tmp_path)
    wait_until_finished(s, "hold")
    with pytest.raises(JobError):
        s.forget("hold")
    assert s.log_path("hold").is_file()


def test_a_corrupt_registry_does_not_take_the_store_down(tmp_path):
    """A half-written file should cost the history, not every future job."""
    s = store(tmp_path)
    (tmp_path / "jobs" / "jobs.json").write_text("{ not json")
    assert s.list() == []
    s.start("fresh", printer("x"), cwd=tmp_path)
    assert wait_until_finished(s, "fresh")["status"] == FINISHED


def test_a_job_that_exited_non_zero_is_failed_not_finished(tmp_path):
    """The difference is the whole reason to look at a job you did not watch."""
    s = store(tmp_path)
    s.start("bad", [sys.executable, "-c", "raise SystemExit(3)"], cwd=tmp_path)
    entry = wait_until_finished(s, "bad")
    assert entry["status"] == "failed" and entry["exit_code"] == 3


def test_a_job_that_exited_cleanly_records_a_zero_exit_code(tmp_path):
    s = store(tmp_path)
    s.start("good", printer("x"), cwd=tmp_path)
    entry = wait_until_finished(s, "good")
    assert entry["status"] == FINISHED and entry["exit_code"] == 0


def test_a_finished_job_leaves_no_zombie_behind(tmp_path):
    """Unreaped children stay in /proc, which is what made every job look alive."""
    from mcp_isicell.backend.jobs import _is_zombie

    s = store(tmp_path)
    entry = s.start("reaped", printer("x"), cwd=tmp_path)
    wait_until_finished(s, "reaped")
    assert not _is_zombie(entry["pid"])
