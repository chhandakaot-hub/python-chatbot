"""Guards on the enrollment models, codes, service and tools.

None of these need the portal database: they pin the column allow-lists, the
code values confirmed against the portal source, the grouping allow-list, and
the query shapes that keep the portal safe.
"""

import pytest
from fastapi import HTTPException

from app.core.database import Base
from app.models.portal.codes import (
    ENROLLMENT_STATUS_LABELS,
    CourseType,
    EnrollmentStatus,
    EnrollmentType,
    label,
)
from app.models.portal.course import Course
from app.models.portal.course_batch import CourseBatch
from app.models.portal.enrollment import Enrollment
from app.services import ai_tools, enrollment_service


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


# --------------------------------------------------------------------------- #
# Column allow-lists
# --------------------------------------------------------------------------- #
def test_enrollment_maps_no_free_text_notes():
    """Staff-typed notes can hold personal circumstances and prompt injections."""
    forbidden = {"comment", "pause_reason", "paused_reason", "deactivation_reason"}
    assert _mapped(Enrollment).isdisjoint(forbidden), _mapped(Enrollment) & forbidden


def test_enrollment_maps_no_commerce_references():
    assert _mapped(Enrollment).isdisjoint({"ls_order_id", "package_id", "reference_package"})


def test_enrollment_maps_no_staff_ids_or_bulky_internals():
    forbidden = {
        "created_by",
        "updated_by",
        "certified_by",
        "batch_assigned_by",
        "passing_criteria",
        "dashboard_journey_steps",
        "certificate_file",
    }
    assert _mapped(Enrollment).isdisjoint(forbidden)


def test_course_maps_no_links_or_staff_ids():
    forbidden = {
        "assignment_instruction_link",
        "assignment_sample_feedback_link",
        "image_path",
        "ai_model_id",
        "default_evaluator_id",
        "student_coach_id",
        "placement_id",
    }
    assert _mapped(Course).isdisjoint(forbidden)


def test_course_batch_maps_no_staff_or_sync_fields():
    forbidden = {"added_by", "updated_by", "edmingle_sync_status", "edmingle_failed_step"}
    assert _mapped(CourseBatch).isdisjoint(forbidden)


def test_portal_tables_never_reach_the_app_base():
    assert {"enrollments", "courses", "course_batches"}.isdisjoint(Base.metadata.tables)


# --------------------------------------------------------------------------- #
# Codes -- values copied from the portal's Laravel constants
# --------------------------------------------------------------------------- #
def test_enrollment_status_codes_match_portal_source():
    # Modules/Enrollment/Entities/Enrollment.php
    assert EnrollmentStatus.PENDING == 0
    assert EnrollmentStatus.ACTIVE == 1
    assert EnrollmentStatus.PAUSED == 2
    assert EnrollmentStatus.RESUME_REQUESTED == 3
    assert EnrollmentStatus.PAUSE_REQUESTED == 4


def test_enrollment_type_codes_match_portal_source():
    assert EnrollmentType.NORMAL == 1
    assert EnrollmentType.PACKAGE == 2
    assert EnrollmentType.BOOTCAMP == 3
    assert EnrollmentType.PACKAGE_BATCH == 4


def test_course_type_codes_match_portal_source():
    # Modules/Course/Entities/Course.php
    assert CourseType.SIMPLE == 1
    assert CourseType.BOOTCAMP == 2


def test_student_status_codes_match_portal_source():
    # Modules/Student/Entities/Student.php -- 0 is pending, not "inactive"
    from app.models.portal.codes import StudentStatus

    assert StudentStatus.PENDING == 0
    assert StudentStatus.ACTIVE == 1
    assert StudentStatus.DISABLED == 2


def test_student_tool_describes_status_with_portal_meanings():
    description = ai_tools._STUDENT_FILTERS["status"].description
    assert "pending" in description and "disabled" in description
    assert "inactive" not in description


def test_labels_accept_varchar_codes_and_report_unknowns():
    assert label(ENROLLMENT_STATUS_LABELS, "2") == "paused"
    assert label(ENROLLMENT_STATUS_LABELS, 9) == "unknown (9)"
    assert label(ENROLLMENT_STATUS_LABELS, None) == "unknown (None)"


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
def test_relationships_must_be_loaded_explicitly():
    """lazy='raise' stops an attribute access firing one query per row."""
    for rel in Enrollment.__mapper__.relationships:
        assert rel.lazy == "raise", rel.key


def test_every_filter_set_excludes_soft_deleted_rows():
    assert "deleted_at IS NULL" in _sql(enrollment_service.build_filters())
    assert "deleted_at IS NULL" in _sql(enrollment_service.build_filters(status=1, completed=True))


def test_paused_means_current_status_not_pause_history():
    """pause_status keeps 'resumed' etc. after the fact; status is the truth."""
    rendered = _sql(enrollment_service.build_filters(paused=True))
    assert "enrollments.status" in rendered
    assert "pause_status" not in rendered


def test_enrollment_type_is_compared_as_the_varchar_it_is_stored_as():
    conditions = enrollment_service.build_filters(enrollment_type=3)
    type_condition = next(c for c in conditions if "enrollments.type" in str(c))
    assert type_condition.right.value == "3"


def test_breakdown_rejects_arbitrary_columns_before_querying():
    # db=None: the allow-list check must fire before any database access.
    with pytest.raises(HTTPException) as exc:
        enrollment_service.breakdown_enrollments(None, "comment")
    assert exc.value.status_code == 400


def test_lists_are_capped():
    from app.services.portal_service import MAX_RESULTS

    assert MAX_RESULTS <= 25
    assert enrollment_service.MAX_PER_STUDENT <= 100


def test_enrollment_output_carries_labels_and_no_free_text():
    from app.schemas.enrollment import EnrollmentOut

    fields = set(EnrollmentOut.model_fields)
    assert {"status_label", "type_label"} <= fields
    assert {"comment", "pause_reason", "deactivation_reason"}.isdisjoint(fields)


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
ENROLLMENT_TOOLS = {
    "get_student_enrollments",
    "search_enrollments",
    "count_enrollments",
    "breakdown_enrollments",
    "get_enrollment",
    "find_courses",
}


def test_enrollment_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert ENROLLMENT_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_invented_enrollment_parameter_is_rejected():
    result = ai_tools.dispatch("count_enrollments", {"course": "Contract"})
    assert "error" in result and "course" in result["error"]


def test_enrollment_breakdown_tool_returns_bad_dimension_as_data():
    result = ai_tools.dispatch("breakdown_enrollments", {"by": "comment"})
    assert "error" in result and "Cannot group by" in result["error"]


def test_model_written_booleans_are_coerced():
    assert ai_tools._bool("true") is True
    assert ai_tools._bool("false") is False
    assert ai_tools._bool(None) is None
