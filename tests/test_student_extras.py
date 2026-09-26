"""Guards and behaviour for pause history, availability, "heard about us",
internal-note counts and the reference lookups."""

from datetime import datetime, time

import pytest
from fastapi import HTTPException

from app.models.portal import reference as ref
from app.models.portal import student_extras as m
from app.services import ai_tools, reference_service, student_extras_service as svc


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


@pytest.fixture(autouse=True)
def _student_exists(monkeypatch):
    """`get_student` needs the students table; these tests are about the extras."""
    monkeypatch.setattr(svc, "get_student", lambda db, student_id: None)


# --------------------------------------------------------------------------- #
# Column allow-lists -- the important half of this module
# --------------------------------------------------------------------------- #
def test_internal_notes_model_cannot_read_the_note_body():
    """The whole point of the count-only view."""
    assert "notes" not in _mapped(m.StudentNote)
    assert _mapped(m.StudentNote) == {"id", "student_id", "created_at", "deleted_at"}


def test_pause_log_maps_no_reason_text_or_ticket_ids():
    forbidden = {
        "paused_reason", "support_ticket_id", "paused_by_student_id", "resumed_by_admin_id",
    }
    assert _mapped(m.EnrollmentPauseLog).isdisjoint(forbidden)


def test_heard_about_answer_omits_the_typed_other_text():
    assert "is_other" not in _mapped(m.HeardAboutAnswer)


def test_raw_registration_json_and_third_party_ids_are_not_modelled():
    import app.models.portal as portal

    tables = set(portal.Student.metadata.tables)
    assert "student_original_registration_details" not in tables
    assert "student_other_details" not in tables
    assert "enrollment_question_answers" not in tables


# --------------------------------------------------------------------------- #
# Pause log
# --------------------------------------------------------------------------- #
def test_pause_status_and_source_are_validated():
    for bad in ({"status": "bogus"}, {"request_source": "parent"}):
        with pytest.raises(HTTPException) as exc:
            svc.build_pause_filters(**bad)
        assert exc.value.status_code == 400


def test_pause_student_filter_goes_through_a_subquery_on_enrollments():
    rendered = _sql(svc.build_pause_filters(student_id=5))
    assert "enrollment_pause_log_new.enrollment_id IN (SELECT enrollments.id" in rendered


def test_pause_breakdown_rejects_arbitrary_columns_before_querying():
    with pytest.raises(HTTPException) as exc:
        svc.breakdown_pause_logs(None, "paused_reason")
    assert exc.value.status_code == 400


def _pause(i, status="paused", source="student", **kw):
    return m.EnrollmentPauseLog(
        id=i, enrollment_id=100 + i, status=status, request_source=source,
        paused_at=datetime(2026, 3, i), created_at=datetime(2026, 3, i), **kw,
    )


def test_pause_filters_search_count_and_breakdown_agree(portal_db):
    portal_db.add_all(
        [
            _pause(1),
            _pause(2, source="support"),
            _pause(3, status="resumed", resumed_at=datetime(2026, 4, 1)),
            _pause(4, status="refund_eligible_paused_requested", rejected=1),
        ]
    )
    portal_db.commit()

    for criteria, expected in [
        ({}, 4),
        ({"status": "paused"}, 2),
        ({"request_source": "support"}, 1),
        ({"open_only": True}, 3),
        ({"open_only": False}, 1),
        ({"enrollment_id": 101}, 1),
    ]:
        assert len(svc.search_pause_logs(portal_db, limit=25, **criteria)) == expected
        assert svc.count_pause_logs(portal_db, **criteria) == expected

    by_status = {g["value"]: g["count"] for g in svc.breakdown_pause_logs(portal_db, "status")}
    assert by_status == {"paused": 2, "resumed": 1, "refund_eligible_paused_requested": 1}


def test_pause_flags_become_real_booleans_and_keep_unknown_as_none(portal_db):
    portal_db.add_all([_pause(1, accepted=1), _pause(2)])
    portal_db.commit()
    rows = {r["id"]: r for r in svc.search_pause_logs(portal_db, limit=25)}
    assert rows[1]["accepted"] is True and rows[1]["rejected"] is None
    assert "paused_reason" not in rows[1]


# --------------------------------------------------------------------------- #
# Availability, heard-about, notes
# --------------------------------------------------------------------------- #
def test_availability_is_ordered_monday_first_with_day_names(portal_db):
    portal_db.add_all(
        [
            m.WeekDay(id=1, name="Monday"),
            m.WeekDay(id=3, name="Wednesday"),
            m.StudentAvailability(id=1, student_id=7, weekday_id=3, timezone="Asia/Kolkata",
                                  start_time=time(18, 0), end_time=time(20, 30)),
            m.StudentAvailability(id=2, student_id=7, weekday_id=1, timezone="Asia/Kolkata",
                                  start_time=time(9, 0), end_time=time(10, 0)),
            m.StudentAvailability(id=3, student_id=8, weekday_id=1, timezone="UTC",
                                  start_time=time(1, 0), end_time=time(2, 0)),
        ]
    )
    portal_db.commit()
    windows = svc.student_availability(portal_db, 7)
    assert [w["weekday"] for w in windows] == ["Monday", "Wednesday"]
    assert windows[1] == {
        "weekday": "Wednesday", "start_time": "18:00", "end_time": "20:30",
        "timezone": "Asia/Kolkata",
    }


def test_heard_about_counts_overall_and_per_student(portal_db):
    portal_db.add_all(
        [
            m.HeardAboutQuestion(id=1, question="YouTube"),
            m.HeardAboutQuestion(id=2, question="Referral"),
            m.HeardAboutAnswer(id=1, student_id=1, answer_id=1),
            m.HeardAboutAnswer(id=2, student_id=2, answer_id=1),
            m.HeardAboutAnswer(id=3, student_id=3, answer_id=2),
        ]
    )
    portal_db.commit()
    assert svc.heard_about(portal_db) == [
        {"value": "YouTube", "count": 2},
        {"value": "Referral", "count": 1},
    ]
    assert svc.heard_about(portal_db, student_id=3) == [{"value": "Referral", "count": 1}]


def test_note_count_ignores_deleted_notes_and_returns_no_text(portal_db):
    portal_db.add_all(
        [
            m.StudentNote(id=1, student_id=7, created_at=datetime(2026, 1, 1)),
            m.StudentNote(id=2, student_id=7, created_at=datetime(2026, 2, 1)),
            m.StudentNote(id=3, student_id=7, created_at=datetime(2026, 3, 1),
                          deleted_at=datetime(2026, 3, 2)),
            m.StudentNote(id=4, student_id=8, created_at=datetime(2026, 4, 1)),
        ]
    )
    portal_db.commit()
    result = svc.note_count(portal_db, 7)
    assert result["notes"] == 2
    assert set(result) == {"student_id", "notes", "latest_note_at"}


# --------------------------------------------------------------------------- #
# Reference lookups
# --------------------------------------------------------------------------- #
def test_reference_kind_and_scoping_are_validated():
    for kwargs in (
        {"kind": "students"},
        {"kind": "country", "country_id": 1},
        {"kind": "tag", "parent_id": 1},
    ):
        with pytest.raises(HTTPException) as exc:
            reference_service.lookup(None, **kwargs)
        assert exc.value.status_code == 400


def test_reference_lookups(portal_db):
    portal_db.add_all(
        [
            ref.Country(id=99, short="IN", name="INDIA", common_name="India", phone_code=91),
            ref.State(id=1, name="Kerala", country_id=99),
            ref.State(id=2, name="Bavaria", country_id=50),
            ref.Country(id=50, short="DE", name="GERMANY", common_name="Germany", phone_code=49),
            ref.Tag(id=1, name='{"en":"Batch Migrated"}', type="enrollment", status=1),
            ref.Tag(id=2, name="plain tag", type="student", status=1),
            ref.Tag(id=3, name="hidden", type="student", status=0),
            ref.CourseCategory(id=1, category_name="Certificate Course", status=1, type="Course"),
            ref.CourseCategory(id=2, category_name="Old", status=1, type="Course",
                               deleted_at=datetime(2025, 1, 1)),
            ref.JobRole(id=1, title="Career Guide"),
        ]
    )
    portal_db.commit()

    assert reference_service.lookup(portal_db, "country", "ind")[0]["phone_code"] == 91
    states = reference_service.lookup(portal_db, "state", country_id=99)
    assert states == [{"id": 1, "name": "Kerala", "country_id": 99, "country": "INDIA"}]
    # translation JSON is unwrapped; inactive tags are hidden
    assert [t["name"] for t in reference_service.lookup(portal_db, "tag")] == [
        "Batch Migrated",
        "plain tag",
    ]
    assert [c["name"] for c in reference_service.lookup(portal_db, "course_category")] == [
        "Certificate Course"
    ]
    assert reference_service.lookup(portal_db, "job_role", "guide") == [
        {"id": 1, "title": "Career Guide"}
    ]


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
EXTRAS_TOOLS = {
    "search_pause_logs", "count_pause_logs", "breakdown_pause_logs",
    "get_student_availability", "heard_about_us", "count_student_notes", "lookup_reference",
}


def test_extras_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert EXTRAS_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_tool_names_are_unique_across_every_module():
    names = [f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations]
    assert len(names) == len(set(names))


def test_no_tool_offers_a_way_to_read_notes_or_reasons():
    for f in ai_tools.TOOL_DECLARATIONS.function_declarations:
        params = set((f.parameters.properties or {}) if f.parameters else {})
        assert params.isdisjoint({"notes", "note", "comment", "reason_text", "paused_reason"}), f.name


def test_invented_extras_parameter_is_rejected():
    result = ai_tools.dispatch("count_pause_logs", {"reason": "illness"})
    assert "error" in result and "reason" in result["error"]


def test_extras_tool_returns_bad_values_as_data():
    assert "status must be" in ai_tools.dispatch("count_pause_logs", {"status": "x"})["error"]
    assert "Unknown kind" in ai_tools.dispatch("lookup_reference", {"kind": "students"})["error"]
