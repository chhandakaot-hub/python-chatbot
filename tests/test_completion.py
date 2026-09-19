"""Guards on course completion.

The rule under test: `enrollments.completed = 1` is not "finished" when the
course requires an LMS MCQ. 53,910 enrollments carry `lms_mcq = Y` and 2,426
sit at `completed = 1, mcq_completed = 0`, so the difference is thousands of
students, not an edge case.
"""

import pytest
from fastapi import HTTPException

from app.models.portal.codes import (
    COMPLETION_AWAITING_MCQ,
    COMPLETION_COMPLETED,
    COMPLETION_NOT_COMPLETED,
)
from app.models.portal.enrollment import Enrollment
from app.services import ai_tools, completion_service


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


def _rendered(expression) -> str:
    """SQL with its values inline -- str() alone leaves bind parameters."""
    return str(expression.compile(compile_kwargs={"literal_binds": True}))


# --------------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------------- #
def test_completed_flag_alone_is_not_completion_when_an_mcq_is_required():
    assert (
        completion_service.completion_state(completed=1, mcq_completed=0, mcq_required=True)
        == COMPLETION_AWAITING_MCQ
    )
    assert (
        completion_service.completion_state(completed=1, mcq_completed=1, mcq_required=True)
        == COMPLETION_COMPLETED
    )


def test_completed_flag_is_enough_when_no_mcq_is_required():
    assert (
        completion_service.completion_state(completed=1, mcq_completed=0, mcq_required=False)
        == COMPLETION_COMPLETED
    )


def test_not_completed_regardless_of_the_mcq():
    for mcq_required in (True, False):
        for mcq_completed in (0, 1, None):
            assert (
                completion_service.completion_state(0, mcq_completed, mcq_required)
                == COMPLETION_NOT_COMPLETED
            )
            assert (
                completion_service.completion_state(None, mcq_completed, mcq_required)
                == COMPLETION_NOT_COMPLETED
            )


def test_the_three_states_are_mutually_exclusive_and_total():
    """Every combination lands in exactly one state, so the summary adds up."""
    seen = set()
    for completed in (None, 0, 1):
        for mcq_completed in (None, 0, 1):
            for mcq_required in (False, True):
                state = completion_service.completion_state(
                    completed, mcq_completed, mcq_required
                )
                assert state in {
                    COMPLETION_COMPLETED,
                    COMPLETION_AWAITING_MCQ,
                    COMPLETION_NOT_COMPLETED,
                }
                seen.add(state)
    assert len(seen) == 3


def test_python_verdict_and_sql_case_use_the_same_three_labels():
    """STATE_CASE groups rows; completion_state() labels one row. If they drift,
    a breakdown and a detail view disagree about the same enrollment."""
    whens = _rendered(completion_service.STATE_CASE)
    for value in (COMPLETION_COMPLETED, COMPLETION_AWAITING_MCQ, COMPLETION_NOT_COMPLETED):
        assert value in whens


def test_awaiting_mcq_is_checked_before_completed_in_the_sql_case():
    """Both conditions hold for a completed=1 MCQ course awaiting confirmation;
    order decides the answer."""
    rendered = _rendered(completion_service.STATE_CASE)
    # The quoted literals, not the bare words: "completed" also occurs in the
    # `enrollments.completed` column name, earlier in the statement.
    assert rendered.index(f"'{COMPLETION_AWAITING_MCQ}'") < rendered.index(
        f"'{COMPLETION_COMPLETED}'"
    )


# --------------------------------------------------------------------------- #
# Where the MCQ requirement comes from
# --------------------------------------------------------------------------- #
def test_the_passing_criteria_blob_stays_unmapped():
    """Only the one lms_mcq key is read, server-side; the blob is never selected."""
    assert "passing_criteria" not in set(Enrollment.__table__.columns.keys())


def test_mcq_requirement_reads_only_the_lms_mcq_key():
    rendered = _rendered(completion_service.MCQ_REQUIRED)
    assert "lms_mcq" in rendered
    assert "JSON_EXTRACT" in rendered


def test_mcq_requirement_accepts_both_spellings_the_portal_writes():
    """The flag is stored as 'Y'/'N' and as '1'/'0'; NULL means not required."""
    rendered = _rendered(completion_service.MCQ_REQUIRED)
    assert "'Y'" in rendered and "'1'" in rendered


def test_completion_percent_columns_are_mapped():
    mapped = set(Enrollment.__table__.columns.keys())
    assert {"subjective_passing_percent", "written_passing_percent"} <= mapped


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
def test_filters_exclude_soft_deleted_rows():
    assert "deleted_at IS NULL" in _sql(completion_service.build_filters())
    assert "deleted_at IS NULL" in _sql(completion_service.build_filters(state="completed"))


def test_unknown_state_is_refused_rather_than_ignored():
    """A dropped state filter would answer for every enrollment instead."""
    with pytest.raises(HTTPException) as exc:
        completion_service.build_filters(state="finished")
    assert exc.value.status_code == 400
    assert "finished" in exc.value.detail


def test_every_state_name_maps_to_a_condition():
    assert set(completion_service.STATES) == {"completed", "awaiting_mcq", "not_completed"}
    for name in completion_service.STATES:
        assert "enrollments" in _sql(completion_service.build_filters(state=name))


def test_breakdown_rejects_arbitrary_columns_before_querying():
    with pytest.raises(HTTPException) as exc:
        completion_service.breakdown_completions(None, "passing_criteria")
    assert exc.value.status_code == 400


def test_lists_are_capped():
    assert completion_service.MAX_PER_STUDENT <= 100


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
COMPLETION_TOOLS = {
    "completion_summary",
    "search_completions",
    "count_completions",
    "breakdown_completions",
    "get_completion",
    "get_student_completion",
}


def test_completion_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert COMPLETION_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_the_model_is_told_the_completed_flag_is_not_the_verdict():
    description = ai_tools._COMPLETION_FILTERS["state"].description
    assert "awaiting_mcq" in description
    assert "not the raw completed column" in description


def test_invented_completion_parameter_is_rejected():
    result = ai_tools.dispatch("count_completions", {"finished": True})
    assert "error" in result and "finished" in result["error"]


def test_unknown_state_reaches_the_model_as_an_error_not_a_wider_answer():
    result = ai_tools.dispatch("count_completions", {"state": "done"})
    assert "error" in result and "done" in result["error"]


def test_completion_breakdown_tool_returns_bad_dimension_as_data():
    result = ai_tools.dispatch("breakdown_completions", {"by": "passing_criteria"})
    assert "error" in result and "Cannot group by" in result["error"]


def test_tool_criteria_cover_every_declared_completion_filter():
    assert set(ai_tools._COMPLETION_FILTERS) == set(ai_tools._completion_criteria({}))
