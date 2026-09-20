"""Rules that exist because codegen itself crashes, not because C++ does."""

from mcp_isicell.domain.models import FunctionCode, ParamInput
from mcp_isicell.domain.validation import validate


def test_function_code_without_a_parseable_name_is_rejected(minimal_model, catalogue):
    """PrimoCell.hpp:182 does an unguarded .group(1) and dies on AttributeError."""
    minimal_model.cell.functions = {"f1": FunctionCode(code="// just a comment")}
    report = validate(minimal_model, catalogue)
    assert "E_FUNC_NO_NAME" in report.codes()


def test_a_full_function_definition_is_accepted(minimal_model, catalogue):
    minimal_model.cell.functions = {
        "f1": FunctionCode(code="void divide() {\n\tthis->init();\n}")
    }
    report = validate(minimal_model, catalogue)
    assert "E_FUNC_NO_NAME" not in report.codes()


def test_a_recorded_attribute_with_no_sql_mapping_is_rejected(minimal_model, catalogue):
    """SchemeSQL.hpp convert() raises Unknown type for anything but scalars/enums."""
    minimal_model.CellAttributesRecorded = [ParamInput(type="string", value="label")]
    report = validate(minimal_model, catalogue)
    assert "E_RECORDED_ATTR_TYPE" in report.codes()


def test_a_recorded_enum_attribute_is_accepted(minimal_model, catalogue):
    minimal_model.enums = {"Phase": ["G1", "S", "G2", "M"]}
    minimal_model.CellAttributesRecorded = [ParamInput(type="Phase", value="phase")]
    report = validate(minimal_model, catalogue)
    assert "E_RECORDED_ATTR_TYPE" not in report.codes()


def test_cell_types_without_common_are_warned_about(minimal_model, catalogue):
    minimal_model.cellTypes = ["Tumour", "Immune"]
    report = validate(minimal_model, catalogue)
    assert "W_NO_COMMON_CELL_TYPE" in report.codes()


def test_scenario_init_code_calling_through_a_pointer_is_rejected(minimal_model, catalogue):
    """codeInit is inlined into Scenario::init, where `s` is not declared."""
    minimal_model.scenario.codeInit = "for(int i=0;i<10;i++) s->createCell();"
    report = validate(minimal_model, catalogue)
    assert "E_SCENARIO_POINTER" in report.codes()


def test_scenario_loop_code_calling_through_a_pointer_is_rejected(minimal_model, catalogue):
    minimal_model.scenario.codeLoop = "s->createCell();"
    report = validate(minimal_model, catalogue)
    assert "E_SCENARIO_POINTER" in report.codes()


def test_scenario_code_calling_a_member_directly_is_accepted(minimal_model, catalogue):
    """The form that actually compiled: the scope already is the Scenario."""
    minimal_model.scenario.codeInit = "for(int i=0;i<10;i++) createCell();"
    report = validate(minimal_model, catalogue)
    assert "E_SCENARIO_POINTER" not in report.codes()


def test_scenario_code_declaring_its_own_s_is_not_flagged(minimal_model, catalogue):
    """The deref is that local variable's, so the rule must not fire on it."""
    minimal_model.scenario.codeInit = "Scenario* s = this; s->createCell();"
    report = validate(minimal_model, catalogue)
    assert "E_SCENARIO_POINTER" not in report.codes()


def test_single_line_function_code_is_warned_about(minimal_model, catalogue):
    """No newline means no metaDataDebug marker, so no error can be attributed."""
    minimal_model.cell.functions = {"f1": FunctionCode(code="void grow() { age += 1.0; }")}
    report = validate(minimal_model, catalogue)
    assert "W_FUNC_SINGLE_LINE" in report.codes()
    # It still compiles, so this must not block a build.
    assert "W_FUNC_SINGLE_LINE" not in [f.code for f in report.errors]


def test_multi_line_function_code_is_not_warned_about(minimal_model, catalogue):
    minimal_model.cell.functions = {"f1": FunctionCode(code="void grow() {\n\tage += 1.0;\n}")}
    report = validate(minimal_model, catalogue)
    assert "W_FUNC_SINGLE_LINE" not in report.codes()
