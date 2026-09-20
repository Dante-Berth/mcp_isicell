"""Authoring takes many calls; a restart must not cost them all."""

import json

from mcp_isicell.domain.models import BuilderData, State
from mcp_isicell.session import VERSION, ModelSession


def authored(path) -> ModelSession:
    s = ModelSession(path=path)
    s.reset("tumour")
    s.replace(BuilderData(cell={"States": [State(name="Proliferating", code="grow();")]}))
    s.set_token("sim_42")
    return s


def test_a_session_is_written_as_soon_as_it_changes(tmp_path):
    path = tmp_path / "session.json"
    authored(path)
    assert path.is_file()
    assert json.loads(path.read_text())["name"] == "tumour"


def test_a_restarted_server_finds_the_model_it_was_authoring(tmp_path):
    path = tmp_path / "session.json"
    authored(path)
    restarted = ModelSession.restored(path)
    assert restarted.name == "tumour"
    assert restarted.model.state_names() == ["Proliferating"]
    assert restarted.token == "sim_42"


def test_the_model_survives_the_round_trip_exactly(tmp_path):
    """Including the `from` alias, which a naive dump would lose."""
    path = tmp_path / "session.json"
    s = ModelSession(path=path)
    s.replace(
        BuilderData.model_validate(
            {
                "cell": {
                    "States": [{"name": "Alive", "code": ""}],
                    "Transitions": [
                        {"from": "Alive", "destinations": [{"to": "DeadCell", "condition": "x"}]}
                    ],
                }
            }
        )
    )
    restored = ModelSession.restored(path)
    assert restored.model.to_generator_json() == s.model.to_generator_json()
    assert restored.model.to_generator_json()["cell"]["Transitions"][0]["from"] == "Alive"


def test_a_compiled_token_is_remembered_so_a_run_still_knows_its_build(tmp_path):
    path = tmp_path / "session.json"
    s = ModelSession(path=path)
    s.set_token("sim_99")
    assert ModelSession.restored(path).token == "sim_99"


def test_starting_with_no_saved_session_gives_an_empty_one(tmp_path):
    s = ModelSession.restored(tmp_path / "absent.json")
    assert s.name == "untitled" and s.model.state_names() == []


def test_a_corrupt_session_is_ignored_rather_than_crashing_the_server(tmp_path):
    path = tmp_path / "session.json"
    path.write_text("{ half written")
    s = ModelSession.restored(path)
    assert s.name == "untitled"


def test_a_corrupt_session_is_left_on_disk_rather_than_deleted(tmp_path):
    """It is the user's model; a confusing restart beats a silent loss."""
    path = tmp_path / "session.json"
    path.write_text("{ half written")
    ModelSession.restored(path)
    assert path.read_text() == "{ half written"


def test_a_session_from_a_future_version_is_not_misread(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"version": VERSION + 1, "name": "newer", "model": {}}))
    assert ModelSession.restored(path).name == "untitled"


def test_a_session_whose_model_no_longer_validates_is_ignored(tmp_path):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({"version": VERSION, "name": "x", "model": {"cell": 7}}))
    assert ModelSession.restored(path).name == "untitled"


def test_autosaving_is_off_when_no_path_is_given(tmp_path):
    """Tests and embedded uses should not have to opt out of touching disk."""
    s = ModelSession()
    s.reset("nowhere")
    assert list(tmp_path.iterdir()) == []


def test_a_save_that_cannot_happen_does_not_fail_the_edit(tmp_path):
    """A full disk must not turn `add a state` into an error."""
    unwritable = tmp_path / "missing" / "deep"
    s = ModelSession(path=unwritable / "session.json")
    (tmp_path / "missing").write_text("a file, not a directory")
    s.reset("still works")  # must not raise
    assert s.name == "still works"


def test_a_reset_clears_what_was_saved_before(tmp_path):
    path = tmp_path / "session.json"
    authored(path)
    ModelSession(path=path).reset("fresh")
    restored = ModelSession.restored(path)
    assert restored.name == "fresh" and restored.token is None
    assert restored.model.state_names() == []
