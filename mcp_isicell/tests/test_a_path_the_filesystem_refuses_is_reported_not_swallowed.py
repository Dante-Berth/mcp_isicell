"""A bad path must reach the caller as a sentence, not as "Error executing tool".

`_tool` turns deliberate failures into ToolError so the explanation survives.
OSError was not in that list, so every tool taking a path had one failure mode
-- the commonest one, a path that does not exist -- where the errno message was
discarded and the caller was told only that something went wrong. These tests
pin the message through, because a path error whose path is missing from it is
no more useful than no message at all.
"""

import pytest

from mcp.server.mcpserver.exceptions import ToolError

from mcp_isicell import server


@pytest.fixture(autouse=True)
def fresh_session():
    server.SESSION.reset("test")
    yield
    server.SESSION.reset("test")


def test_saving_into_a_directory_that_does_not_exist_names_the_path(tmp_path):
    """The mistake that costs a turn: a plausible path under a missing parent."""
    target = tmp_path / "no-such-dir" / "model.json"
    with pytest.raises(ToolError) as excinfo:
        server.isicell_model_save(str(target))
    assert str(target) in str(excinfo.value)


def test_loading_a_model_that_is_not_there_names_the_path(tmp_path):
    missing = tmp_path / "absent.json"
    with pytest.raises(ToolError) as excinfo:
        server.isicell_model_load(str(missing))
    assert str(missing) in str(excinfo.value)


def test_a_directory_given_where_a_file_was_meant_is_refused(tmp_path):
    """IsADirectoryError is an OSError too, and reads as unhelpfully when bare."""
    with pytest.raises(ToolError):
        server.isicell_model_save(str(tmp_path))


def test_a_path_error_does_not_disturb_the_session(tmp_path):
    """A refused save leaves the model being authored exactly as it was.

    The same guarantee authoring edits give: a rejected call cannot corrupt the
    session. Worth pinning here because `save` reads the model to serialise it.
    """
    server.isicell_model_new("tumour")
    server.isicell_model_add_state("Alive")
    before = server.isicell_model_export()
    with pytest.raises(ToolError):
        server.isicell_model_save(str(tmp_path / "gone" / "m.json"))
    assert server.isicell_model_export() == before


def test_a_saveable_path_still_saves(tmp_path):
    """The guard must not have turned a working call into a refusal."""
    server.isicell_model_new("tumour")
    target = tmp_path / "model.json"
    assert server.isicell_model_save(str(target))["written"] == str(target)
    assert target.is_file()
