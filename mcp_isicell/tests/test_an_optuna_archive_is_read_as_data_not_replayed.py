"""Reading an Optuna archive without executing anything it carries.

The archives are built here as raw SQLite on Optuna's own schema, so the suite
still needs nothing installed. `test_a_real_optuna_study_reads_the_same_way`
runs the same assertions against a study Optuna itself wrote, and is skipped
where Optuna is absent -- that is the test that would catch the schema drifting
away from the one assumed here.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mcp_isicell import server
from mcp.server.mcpserver.exceptions import ToolError

SCHEMA = """
CREATE TABLE studies (study_id INTEGER PRIMARY KEY, study_name TEXT);
CREATE TABLE study_directions (
    study_direction_id INTEGER PRIMARY KEY, direction TEXT,
    study_id INTEGER, objective INTEGER
);
CREATE TABLE trials (
    trial_id INTEGER PRIMARY KEY, number INTEGER, study_id INTEGER, state TEXT
);
CREATE TABLE trial_values (
    trial_value_id INTEGER PRIMARY KEY, trial_id INTEGER,
    objective INTEGER, value REAL
);
CREATE TABLE trial_params (
    param_id INTEGER PRIMARY KEY, trial_id INTEGER,
    param_name TEXT, param_value REAL, distribution_json TEXT
);
"""

FLOAT_DIST = json.dumps(
    {"name": "FloatDistribution", "attributes": {"low": 0.0, "high": 10.0}}
)
INT_DIST = json.dumps({"name": "IntDistribution", "attributes": {"low": 1, "high": 9}})
CHOICES = ["slow", "medium", "fast"]
CATEGORICAL_DIST = json.dumps(
    {"name": "CategoricalDistribution", "attributes": {"choices": CHOICES}}
)


@pytest.fixture
def archive(tmp_path) -> str:
    """One minimised study of three trials, one of them failed."""
    path = tmp_path / "study.db"
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO studies VALUES (1, 'growth')")
    conn.execute("INSERT INTO study_directions VALUES (1, 'MINIMIZE', 1, 0)")
    conn.executemany(
        "INSERT INTO trials VALUES (?,?,?,?)",
        [(10, 0, 1, "COMPLETE"), (11, 1, 1, "COMPLETE"), (12, 2, 1, "FAIL")],
    )
    conn.executemany(
        "INSERT INTO trial_values VALUES (?,?,?,?)",
        [(1, 10, 0, 4.0), (2, 11, 0, 1.5)],
    )
    conn.executemany(
        "INSERT INTO trial_params VALUES (?,?,?,?,?)",
        [
            (1, 10, "cycleDuration", 12.0, FLOAT_DIST),
            (2, 10, "seedCount", 3.0, INT_DIST),
            # Optuna stores a categorical as the index of its choice.
            (3, 10, "mode", 2.0, CATEGORICAL_DIST),
            (4, 11, "cycleDuration", 20.0, FLOAT_DIST),
            (5, 11, "seedCount", 7.0, INT_DIST),
            (6, 11, "mode", 0.0, CATEGORICAL_DIST),
        ],
    )
    conn.commit()
    conn.close()
    return str(path)


def test_a_studys_trials_are_counted_by_state(archive):
    studies = server.isicell_optuna_studies(archive)
    assert len(studies) == 1
    assert studies[0]["study"] == "growth"
    assert studies[0]["byState"] == {"COMPLETE": 2, "FAIL": 1}
    assert studies[0]["directions"] == ["minimize"]


def test_only_completed_trials_are_returned_by_default(archive):
    """A failed trial has no objective value, so ranking it would be a lie."""
    assert [t["number"] for t in server.isicell_optuna_trials(archive, "growth")] == [0, 1]
    every = server.isicell_optuna_trials(archive, "growth", state=None)
    assert [t["state"] for t in every] == ["COMPLETE", "COMPLETE", "FAIL"]


def test_a_categorical_parameter_comes_back_as_its_value_not_its_index(archive):
    """The decode Optuna's own distribution makes possible, with no archived code."""
    assert server.isicell_optuna_parameters(archive, 10)["mode"] == "fast"
    assert server.isicell_optuna_parameters(archive, 11)["mode"] == "slow"


def test_an_int_parameter_is_not_reported_as_a_float(archive):
    params = server.isicell_optuna_parameters(archive, 11)
    assert params["seedCount"] == 7 and isinstance(params["seedCount"], int)
    assert params["cycleDuration"] == 20.0


def test_the_best_trial_of_a_minimised_study_is_the_lowest(archive):
    best = server.isicell_optuna_best(archive, "growth", n=1)
    assert best[0]["trial_id"] == 11 and best[0]["values"] == [1.5]
    assert best[0]["direction"] == "minimize"


def test_a_maximised_study_is_ranked_the_other_way(tmp_path, archive):
    """The direction is read from the archive, never assumed."""
    conn = sqlite3.connect(archive)
    conn.execute("UPDATE study_directions SET direction = 'MAXIMIZE'")
    conn.commit()
    conn.close()
    best = server.isicell_optuna_best(archive, "growth", n=1)
    assert best[0]["trial_id"] == 10 and best[0]["direction"] == "maximize"


def test_an_archive_without_distributions_still_reads(tmp_path):
    """An older archive lacks distribution_json; values come back undecoded."""
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        SCHEMA.replace(
            "param_name TEXT, param_value REAL, distribution_json TEXT",
            "param_name TEXT, param_value REAL",
        )
    )
    conn.execute("INSERT INTO studies VALUES (1, 's')")
    conn.execute("INSERT INTO trials VALUES (10, 0, 1, 'COMPLETE')")
    conn.execute("INSERT INTO trial_values VALUES (1, 10, 0, 2.0)")
    conn.execute("INSERT INTO trial_params VALUES (1, 10, 'x', 5.0)")
    conn.commit()
    conn.close()
    assert server.isicell_optuna_parameters(str(path), 10) == {"x": 5.0}


def test_a_results_database_is_not_mistaken_for_an_optuna_archive(tmp_path):
    """The two are both SQLite; saying which tool to use costs one sentence."""
    path = tmp_path / "sweep.db"
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE parameters ("ID_PARAMETER" INTEGER)')
    conn.commit()
    conn.close()
    with pytest.raises(ToolError) as excinfo:
        server.isicell_optuna_studies(str(path))
    assert "isicell_results_" in str(excinfo.value)


def test_a_missing_archive_is_refused(tmp_path):
    with pytest.raises(ToolError):
        server.isicell_optuna_studies(str(tmp_path / "absent.db"))


def test_an_unknown_study_is_refused_and_names_the_real_ones(archive):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_optuna_trials(archive, "nope")
    assert "growth" in str(excinfo.value)


def test_an_objective_a_study_does_not_have_is_refused(archive):
    with pytest.raises(ToolError) as excinfo:
        server.isicell_optuna_best(archive, "growth", objective=3)
    assert "1 objective" in str(excinfo.value)


def test_a_real_optuna_study_reads_the_same_way(tmp_path):
    """Against a study Optuna wrote, so the assumed schema is checked, not trusted."""
    optuna = pytest.importorskip("optuna")
    optuna.logging.set_verbosity(optuna.logging.WARNING)
    path = Path(tmp_path) / "real.db"
    study = optuna.create_study(
        study_name="real", storage=f"sqlite:///{path}", direction="minimize"
    )

    def objective(trial):
        x = trial.suggest_float("x", 0.0, 10.0)
        n = trial.suggest_int("n", 1, 9)
        mode = trial.suggest_categorical("mode", CHOICES)
        return abs(x - 3.0) + n + (0.0 if mode == "fast" else 1.0)

    study.optimize(objective, n_trials=6)

    studies = server.isicell_optuna_studies(str(path))
    assert [s["study"] for s in studies] == ["real"]
    assert studies[0]["directions"] == ["minimize"]
    assert studies[0]["byState"]["COMPLETE"] == 6

    trials = server.isicell_optuna_trials(str(path), "real")
    assert len(trials) == 6
    assert all(set(t["params"]) == {"x", "n", "mode"} for t in trials)
    # The decode is the part that could rot: a categorical must not come back
    # as the index Optuna stored.
    assert all(t["params"]["mode"] in CHOICES for t in trials)
    assert all(isinstance(t["params"]["n"], int) for t in trials)

    best = server.isicell_optuna_best(str(path), "real", n=1)[0]
    assert best["values"][0] == pytest.approx(study.best_value)
    assert server.isicell_optuna_parameters(str(path), best["trial_id"]) == best["params"]
    assert best["params"] == study.best_params
