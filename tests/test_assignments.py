"""Guards on the assignment and student-assignment models, service and tools.

None of these need the portal database: they pin the column allow-lists, the
code values confirmed against the portal source, the grouping allow-list, and
the query shapes that keep the portal safe.
"""

import pytest
from fastapi import HTTPException

from app.core.database import Base
from app.models.portal.assignment import Assignment
from app.models.portal.codes import (
    ASSIGNMENT_TYPE_LABELS,
    AssignmentStatus,
    AssignmentType,
    Plagiarism,
    StudentAssignmentStatus,
    label,
)
from app.models.portal.student_assignment import StudentAssignment
from app.models.portal.topic import Topic
from app.services import ai_tools, assignment_service


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


# --------------------------------------------------------------------------- #
# Column allow-lists
# --------------------------------------------------------------------------- #
def test_assignment_maps_no_content_links_or_files():
    """The assignment's own material is not the bot's to hand on."""
    forbidden = {
        "assignment_instruction_link",
        "assignment_sample_feedback_link",
        "assignment_download_file",
        "allowed_file_types",
    }
    assert _mapped(Assignment).isdisjoint(forbidden), _mapped(Assignment) & forbidden


def test_assignment_maps_no_ai_config_staff_ids_or_commerce():
    forbidden = {"ai_model_id", "is_ai_enabled", "created_by", "updated_by", "package_id"}
    assert _mapped(Assignment).isdisjoint(forbidden), _mapped(Assignment) & forbidden


def test_student_assignment_maps_no_links_ai_config_or_staff_ids():
    forbidden = {
        "assignment_instruction_link",
        "assignment_sample_feedback_link",
        "ai_model_id",
        "is_ai_enabled",
        "created_by",
        "updated_by",
    }
    assert _mapped(StudentAssignment).isdisjoint(forbidden)


def test_topic_maps_only_id_and_title():
    assert _mapped(Topic) == {"id", "title"}


def test_new_portal_tables_never_reach_the_app_base():
    assert {"assignments", "student_assignments", "topics"}.isdisjoint(Base.metadata.tables)


def test_assignment_schemas_expose_no_unmapped_columns():
    from app.schemas.assignment import AssignmentOut

    # course_name/topic/labels are derived, everything else must be a real column.
    derived = {"course_name", "topic", "type_label", "status_label", "plagiarism_checked"}
    fields = set(AssignmentOut.model_fields) - derived
    assert fields <= _mapped(Assignment), fields - _mapped(Assignment)


# --------------------------------------------------------------------------- #
# Codes -- values copied from the portal's Laravel constants
# --------------------------------------------------------------------------- #
def test_assignment_codes_match_portal_source():
    # Modules/Assignment/Entities/Assignment.php
    assert AssignmentType.SUBJECTIVE == 0
    assert AssignmentType.WRITTEN == 1
    assert AssignmentStatus.DEACTIVE == 0
    assert AssignmentStatus.ACTIVE == 1
    assert Plagiarism.NO == 0
    assert Plagiarism.YES == 1


def test_student_assignment_status_codes_match_portal_source():
    # Modules/StudentAssignment/Entities/StudentAssignment.php
    assert StudentAssignmentStatus.DEACTIVE == 0
    assert StudentAssignmentStatus.ACTIVE == 1
    assert StudentAssignmentStatus.PENDING == 2
    assert StudentAssignmentStatus.SUBMITTED == 3
    assert StudentAssignmentStatus.RESUBMITTED == 4
    assert StudentAssignmentStatus.EVALUATED == 5


def test_subjective_is_a_real_type_not_a_missing_value():
    """assignment_type 0 is 'subjective', about half the table. Treating it as
    unset would make it unfilterable."""
    assert label(ASSIGNMENT_TYPE_LABELS, 0) == "subjective"
    conditions = assignment_service.build_assignment_filters(assignment_type=0)
    assert "assignments.assignment_type" in _sql(conditions)


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
def test_relationships_must_be_loaded_explicitly():
    """lazy='raise' stops an attribute access firing one query per row."""
    for model in (Assignment, StudentAssignment):
        for rel in model.__mapper__.relationships:
            assert rel.lazy == "raise", f"{model.__name__}.{rel.key}"


def test_student_assignment_filters_exclude_soft_deleted_rows():
    assert "deleted_at IS NULL" in _sql(assignment_service.build_student_assignment_filters())
    assert "deleted_at IS NULL" in _sql(
        assignment_service.build_student_assignment_filters(submitted=True)
    )


def test_submitted_is_a_status_set_not_merely_not_deactivated():
    """4.35M of 4.0M+ rows sit at status 1 -- switched on, nothing handed in.
    Reading 'submitted' as 'not deactivated' would report almost the whole
    portal as submitted."""
    assert set(assignment_service.SUBMITTED_STATUSES) == {
        StudentAssignmentStatus.SUBMITTED,
        StudentAssignmentStatus.RESUBMITTED,
        StudentAssignmentStatus.EVALUATED,
    }
    assert StudentAssignmentStatus.ACTIVE not in assignment_service.SUBMITTED_STATUSES
    rendered = _sql(assignment_service.build_student_assignment_filters(submitted=True))
    assert "IN (" in rendered


def test_overdue_excludes_work_already_handed_in():
    """A past deadline on an evaluated assignment is history, not a problem."""
    rendered = _sql(assignment_service.build_student_assignment_filters(overdue=True))
    assert "submission_last_date" in rendered
    assert "NOT IN" in rendered or "not in" in rendered.lower()


def test_student_id_filter_reaches_through_the_enrollment():
    """student_assignments has no student_id column of its own."""
    assert "student_id" not in _mapped(StudentAssignment)
    rendered = _sql(assignment_service.build_student_assignment_filters(student_id=7))
    assert "enrollments.student_id" in rendered


def test_breakdowns_reject_arbitrary_columns_before_querying():
    # db=None: the allow-list check must fire before any database access.
    for fn in (
        assignment_service.breakdown_assignments,
        assignment_service.breakdown_student_assignments,
    ):
        with pytest.raises(HTTPException) as exc:
            fn(None, "created_by")
        assert exc.value.status_code == 400


def test_lists_are_capped():
    from app.services.portal_service import MAX_RESULTS

    assert MAX_RESULTS <= 25
    assert assignment_service.MAX_PER_ENROLLMENT <= 100


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
ASSIGNMENT_TOOLS = {
    "search_assignments",
    "count_assignments",
    "breakdown_assignments",
    "get_assignment",
    "search_student_assignments",
    "count_student_assignments",
    "breakdown_student_assignments",
    "get_enrollment_assignments",
}


def test_assignment_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert ASSIGNMENT_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_invented_assignment_parameter_is_rejected():
    result = ai_tools.dispatch("count_assignments", {"instructions": "x"})
    assert "error" in result and "instructions" in result["error"]


def test_assignment_breakdown_tool_returns_bad_dimension_as_data():
    result = ai_tools.dispatch("breakdown_assignments", {"by": "assignment_download_file"})
    assert "error" in result and "Cannot group by" in result["error"]


def test_submitted_filter_is_described_so_the_model_does_not_use_status():
    description = ai_tools._STUDENT_ASSIGNMENT_FILTERS["submitted"].description
    assert "3, 4 or 5" in description
    assert "switched on" in description


def test_tool_criteria_cover_every_declared_filter():
    """A filter the model can send but the coercion drops would silently widen
    the answer."""
    for declared, coerce in (
        (ai_tools._ASSIGNMENT_FILTERS, ai_tools._assignment_criteria),
        (ai_tools._STUDENT_ASSIGNMENT_FILTERS, ai_tools._student_assignment_criteria),
    ):
        assert set(declared) == set(coerce({}))
