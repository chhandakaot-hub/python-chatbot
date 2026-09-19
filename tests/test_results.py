"""Guards on the result model, service and tools.

The important one is `test_results_join_student_assignments_not_assignments`:
the portal's `results.assignment_id` is a student_assignments id, and joining
it to `assignments` returns rows about the wrong assignment rather than
failing.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.database import Base
from app.models.portal.codes import RESULT_STATUS_LABELS, ResultStatus, label
from app.models.portal.result import Result
from app.models.portal.student_assignment import StudentAssignment
from app.services import ai_tools, result_service


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


# --------------------------------------------------------------------------- #
# The join everything here depends on
# --------------------------------------------------------------------------- #
def test_results_join_student_assignments_not_assignments():
    """`Result::belongsTo(StudentAssignment::class, 'assignment_id')`.

    All 123,355 result rows join to student_assignments; 106,712 also collide
    with an assignments id, so the wrong join silently returns plausible rows
    about a different assignment.
    """
    target = Result.__table__.c.assignment_id.foreign_keys
    assert {fk.column.table.name for fk in target} == {"student_assignments"}

    rendered = str(select(Result).join(Result.student_assignment))
    assert "JOIN student_assignments" in rendered


def test_result_relationship_is_named_for_what_it_points_at():
    assert "student_assignment" in Result.__mapper__.relationships
    assert Result.__mapper__.relationships["student_assignment"].mapper.class_ is StudentAssignment


def test_result_schema_renames_the_misleading_column():
    from app.schemas.result import ResultSummary

    fields = set(ResultSummary.model_fields)
    assert "student_assignment_id" in fields
    assert "assignment_id" not in fields


# --------------------------------------------------------------------------- #
# Column allow-lists
# --------------------------------------------------------------------------- #
def test_result_maps_no_feedback_text():
    """Written about a named student, and a prompt-injection vector."""
    forbidden = {
        "ai_feedback",
        "reviewer_edited_feedback",
        "feedback_to_student",
        "resubmission_feedback",
        "reason",
        "feedback_edit_reason",
    }
    assert _mapped(Result).isdisjoint(forbidden), _mapped(Result) & forbidden


def test_result_maps_no_files_or_links():
    forbidden = {
        "submitted_file",
        "feedback_file",
        "feedback_file_original_name",
        "feedback_link",
        "ai_feedback_pdf_url",
        "plagiarism_result_file",
    }
    assert _mapped(Result).isdisjoint(forbidden), _mapped(Result) & forbidden


def test_result_maps_no_bulky_internals_or_staff_ids():
    forbidden = {
        "unicheck_details",
        "unicheck_file_details",
        "unicheck_check_details",
        "auto_assignment_response",
        "ai_evaluation_uuid",
        "ai_model_id",
        "ai_instruction_source",
        "ai_feedback_sample_source",
        "evaluator_id",
        "reviewed_by",
        "updated_by",
    }
    assert _mapped(Result).isdisjoint(forbidden), _mapped(Result) & forbidden


def test_result_output_carries_no_free_text():
    from app.schemas.result import ResultOut

    fields = set(ResultOut.model_fields)
    forbidden = {"ai_feedback", "feedback_to_student", "reason", "resubmission_feedback"}
    assert forbidden.isdisjoint(fields)
    assert "status_label" in fields


def test_results_table_never_reaches_the_app_base():
    assert "results" not in Base.metadata.tables


# --------------------------------------------------------------------------- #
# Codes
# --------------------------------------------------------------------------- #
def test_result_status_codes_match_portal_source():
    # Modules/Result/Entities/Result.php
    assert ResultStatus.DEACTIVE == 0
    assert ResultStatus.ACTIVE == 1
    assert ResultStatus.PENDING == 2
    assert ResultStatus.RESUBMIT == 3
    assert ResultStatus.EVALUATED == 5


def test_result_status_four_does_not_exist():
    """The portal's constants skip 4; inventing a label would be a guess."""
    assert 4 not in RESULT_STATUS_LABELS
    assert label(RESULT_STATUS_LABELS, 4) == "unknown (4)"


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
def test_relationships_must_be_loaded_explicitly():
    for rel in Result.__mapper__.relationships:
        assert rel.lazy == "raise", rel.key


def test_every_filter_set_excludes_soft_deleted_rows():
    assert "deleted_at IS NULL" in _sql(result_service.build_filters())
    assert "deleted_at IS NULL" in _sql(result_service.build_filters(evaluated=True))


def test_score_prefers_the_reviewers_correction_over_the_ai_score():
    """Filtering and reporting must agree on which score counts."""
    rendered = _sql(result_service.build_filters(min_score=50))
    assert "coalesce" in rendered.lower()
    assert "reviewer_edited_score" in rendered
    assert rendered.index("reviewer_edited_score") < rendered.index("ai_score")


def test_latest_only_distinguishes_submissions_from_assignments():
    """A resubmitted assignment has several rows; counting them all answers a
    different question."""
    rendered = _sql(result_service.build_filters(latest_only=True))
    assert "results.latest" in rendered


def test_overdue_evaluation_excludes_already_evaluated_work():
    rendered = _sql(result_service.build_filters(overdue_evaluation=True))
    assert "evaluation_due_date" in rendered
    assert "evaluation_date IS NULL" in rendered


def test_breakdown_rejects_arbitrary_columns_before_querying():
    with pytest.raises(HTTPException) as exc:
        result_service.breakdown_results(None, "ai_feedback")
    assert exc.value.status_code == 400


def test_lists_are_capped():
    assert result_service.MAX_PER_STUDENT <= 100


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
RESULT_TOOLS = {
    "search_results",
    "count_results",
    "breakdown_results",
    "get_result",
    "get_student_results",
}


def test_result_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert RESULT_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_invented_result_parameter_is_rejected():
    result = ai_tools.dispatch("count_results", {"feedback": "good"})
    assert "error" in result and "feedback" in result["error"]


def test_result_breakdown_tool_returns_bad_dimension_as_data():
    result = ai_tools.dispatch("breakdown_results", {"by": "ai_feedback"})
    assert "error" in result and "Cannot group by" in result["error"]


def test_latest_only_is_explained_to_the_model():
    description = ai_tools._RESULT_FILTERS["latest_only"].description
    assert "resubmission" in description.lower()


def test_tool_criteria_cover_every_declared_result_filter():
    assert set(ai_tools._RESULT_FILTERS) == set(ai_tools._result_criteria({}))
