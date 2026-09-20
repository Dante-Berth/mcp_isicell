"""The serialised form is a wire format: mainGenerator.py indexes it by name."""

from mcp_isicell.domain.models import BuilderData, FunctionCode


def test_transition_source_serialises_back_to_the_from_key(minimal_model):
    """`from` is a Python keyword, so the field is from_ -- but the JSON must not be."""
    payload = minimal_model.to_generator_json()
    assert payload["cell"]["Transitions"][0]["from"] == "Proliferating"
    assert "from_" not in payload["cell"]["Transitions"][0]


def test_every_top_level_builder_data_key_is_present(minimal_model):
    """mainGenerator.py and the isigen blocks index these by name; none may vanish."""
    payload = minimal_model.to_generator_json()
    assert set(payload) == {
        "moduleSelected",
        "customModule",
        "enums",
        "cellTypes",
        "scenario",
        "cell",
        "paramInputs",
        "CellAttributesRecorded",
        "CellFunctionsRecorded",
    }


def test_a_model_survives_a_json_round_trip(minimal_model):
    again = BuilderData.model_validate(minimal_model.to_generator_json())
    assert again.to_generator_json() == minimal_model.to_generator_json()


def test_an_empty_cell_types_list_means_a_single_common_type():
    """Model.hpp:10 generates `enum Type { Common }` when cellTypes is empty."""
    assert BuilderData().effective_cell_types() == ["Common"]


def test_functions_serialise_as_bare_strings_not_objects(minimal_model):
    """PrimoCell.hpp:182 calls code.replace() on each value, so a dict kills codegen.

    The failure is opaque -- Isigen's exec raises
    `AttributeError: 'dict' object has no attribute 'replace'` -- and it also
    suppresses the metaInfo map that maps g++ errors back onto the model.
    """
    minimal_model.cell.functions = {"f1": FunctionCode(code="void grow() {\n\tage += 1.0;\n}")}
    minimal_model.scenario.functions = {"f2": FunctionCode(code="void seed() {\n\tcreateCell();\n}")}
    payload = minimal_model.to_generator_json()
    assert payload["cell"]["functions"]["f1"] == "void grow() {\n\tage += 1.0;\n}"
    assert payload["scenario"]["functions"]["f2"] == "void seed() {\n\tcreateCell();\n}"


def test_a_model_with_functions_still_survives_a_json_round_trip(minimal_model):
    """Flattening is a boundary concern; re-validating the payload must still work."""
    minimal_model.cell.functions = {"f1": FunctionCode(code="void grow() {\n\tage += 1.0;\n}")}
    again = BuilderData.model_validate(minimal_model.to_generator_json())
    assert again.cell.functions["f1"].code == minimal_model.cell.functions["f1"].code
