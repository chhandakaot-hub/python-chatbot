"""Guards and behaviour for portal staff, roles and course assignments."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models.portal import staff as m
from app.models.portal.codes import USER_STATUS_LABELS, UserStatus, label
from app.models.portal.course import Course
from app.services import ai_tools, staff_service


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


# --------------------------------------------------------------------------- #
# Column allow-lists and codes
# --------------------------------------------------------------------------- #
def test_staff_maps_no_contact_details_or_credentials():
    forbidden = {
        "email", "phone", "meeting_email", "password", "remember_token", "calendly_link",
        "tmp_verification_token", "tmp_verification_token_expire_at", "forum_text",
        "forum_token_time", "edmingle_id", "kanboard_id", "email_verified_at",
    }
    assert _mapped(m.Staff).isdisjoint(forbidden), _mapped(m.Staff) & forbidden


def test_staff_rows_never_carry_contact_keys(portal_db):
    portal_db.add(m.Staff(id=1, full_name="A B", status=1))
    portal_db.commit()
    row = staff_service.find_staff(portal_db)[0]
    assert {"email", "phone", "password"}.isdisjoint(row)


def test_user_status_codes_match_portal_source():
    # app/Models/User.php -- note 0 is disabled and 1 approved, 2 blocked, 3 pending
    assert (UserStatus.DISABLED, UserStatus.APPROVED) == (0, 1)
    assert (UserStatus.BLOCKED, UserStatus.PENDING) == (2, 3)
    assert label(USER_STATUS_LABELS, 3) == "pending"
    assert label(USER_STATUS_LABELS, 9) == "unknown (9)"


def test_every_staff_filter_set_excludes_soft_deleted_accounts():
    assert "deleted_at IS NULL" in _sql(staff_service.build_filters())
    assert "deleted_at IS NULL" in _sql(staff_service.build_filters(role="Evaluator", status=1))


def test_breakdown_rejects_arbitrary_columns_before_querying():
    with pytest.raises(HTTPException) as exc:
        staff_service.breakdown_staff(None, "email")
    assert exc.value.status_code == 400


# --------------------------------------------------------------------------- #
# Behaviour against a real (SQLite) database
# --------------------------------------------------------------------------- #
@pytest.fixture
def staffed(portal_db):
    portal_db.add_all(
        [
            m.Staff(id=1, full_name=" Asha Rao", status=1),
            m.Staff(id=2, full_name="Ben Roy", status=1),
            m.Staff(id=3, full_name="Chitra Sen", status=0),
            m.Staff(id=4, full_name="Deleted Dan", status=1, deleted_at=datetime(2025, 1, 1)),
            m.Role(id=1, name="Evaluator", status=1),
            m.Role(id=2, name="Admin", status=1),
            m.StaffRole(role_id=1, model_type=m.STAFF_MODEL_TYPE, model_id=1),
            m.StaffRole(role_id=2, model_type=m.STAFF_MODEL_TYPE, model_id=1),
            m.StaffRole(role_id=2, model_type=m.STAFF_MODEL_TYPE, model_id=2),
            m.StaffRole(role_id=1, model_type=m.STAFF_MODEL_TYPE, model_id=4),
            # a role held by some other kind of model must not be counted
            m.StaffRole(role_id=1, model_type="App\\Models\\Student", model_id=2),
            Course(id=10, course_name="Contract Drafting", status=1, duration_days=90,
                   course_type=1, is_job_eligible=0),
            m.CourseEvaluator(id=1, course_id=10, evaluator_id=1),
            m.CourseInstructor(id=1, course_id=10, instructor_id=2),
            m.CourseInstructor(id=2, course_id=10, instructor_id=3, deleted_at=datetime(2025, 1, 1)),
            m.CourseMentor(id=1, course_id=10, mentor_id=3),
        ]
    )
    portal_db.commit()
    return portal_db


def test_find_staff_by_role_returns_roles_and_trims_names(staffed):
    people = staff_service.find_staff(staffed, role="Evaluator")
    assert [p["full_name"] for p in people] == ["Asha Rao"]  # leading space trimmed
    assert people[0]["roles"] == ["Admin", "Evaluator"]
    assert people[0]["status_label"] == "approved"


def test_deleted_staff_never_appear(staffed):
    assert staff_service.count_staff(staffed) == 3
    assert "Deleted Dan" not in [p["full_name"] for p in staff_service.find_staff(staffed)]


def test_count_and_list_agree(staffed):
    for criteria in ({}, {"status": 1}, {"role": "Admin"}, {"query": "en"}):
        assert len(staff_service.find_staff(staffed, limit=25, **criteria)) == staff_service.count_staff(
            staffed, **criteria
        )


def test_role_breakdown_counts_people_not_other_model_types(staffed):
    groups = {g["value"]: g["count"] for g in staff_service.breakdown_staff(staffed, "role")}
    assert groups == {"Admin": 2, "Evaluator": 1}
    by_status = staff_service.breakdown_staff(staffed, "status")
    assert {g["label"]: g["count"] for g in by_status} == {"approved": 2, "disabled": 1}


def test_course_staff_hides_removed_instructors(staffed):
    result = staff_service.course_staff(staffed, 10)
    assert [p["full_name"] for p in result["evaluators"]] == ["Asha Rao"]
    assert [p["full_name"] for p in result["instructors"]] == ["Ben Roy"]
    assert [p["full_name"] for p in result["mentors"]] == ["Chitra Sen"]


def test_staff_courses_lists_each_relationship(staffed):
    result = staff_service.staff_courses(staffed, 1)
    assert result["evaluates"] == [{"course_id": 10, "course_name": "Contract Drafting"}]
    assert result["instructs"] == [] and result["mentors"] == []
    assert result["roles"] == ["Admin", "Evaluator"]


def test_unknown_course_or_staff_is_a_404(staffed):
    for call in (
        lambda: staff_service.course_staff(staffed, 999),
        lambda: staff_service.staff_courses(staffed, 999),
        lambda: staff_service.staff_courses(staffed, 4),  # soft-deleted
    ):
        with pytest.raises(HTTPException) as exc:
            call()
        assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
STAFF_TOOLS = {
    "find_staff", "count_staff", "breakdown_staff", "get_course_staff", "get_staff_courses",
}


def test_staff_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert STAFF_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_staff_tool_describes_status_with_portal_meanings():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    description = declared["find_staff"].parameters.properties["status"].description
    assert "blocked" in description and "pending" in description


def test_staff_tools_offer_no_contact_lookup():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    params = set(declared["find_staff"].parameters.properties)
    assert params.isdisjoint({"email", "phone"})
    assert "error" in ai_tools.dispatch("find_staff", {"email": "a@b.c"})
