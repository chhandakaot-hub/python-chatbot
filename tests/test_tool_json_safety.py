"""Tool results go to Gemini as JSON; a raw date or datetime crashes the chat turn.

This slipped through once because the checks only printed results as text. So
every tool that can return a temporal value is run here against data that
contains one, and the result must survive `json.dumps` with no `default=`.
"""

import json
from contextlib import contextmanager
from datetime import date, datetime, time

import pytest

from app.models.portal import catalog, feedback, staff, student_extras
from app.models.portal.course import Course
from app.services import ai_tools, ai_tools_extra, student_extras_service


@pytest.fixture
def seeded(portal_db, monkeypatch):
    @contextmanager
    def session():
        yield portal_db

    monkeypatch.setattr(ai_tools_extra, "PortalSessionLocal", session)
    monkeypatch.setattr(student_extras_service, "get_student", lambda db, student_id: None)

    when = datetime(2026, 3, 4, 5, 6, 7)
    portal_db.add_all(
        [
            feedback.AssignmentCsat(id=1, student_id=1, assignment_id=1, course_id=1, rating=5,
                                    created_at=when),
            feedback.ClassCsat(id=1, student_id=1, class_date_relation_id=1, rating=4, status="A",
                               created_at=when),
            feedback.NpsForm(id=1, student_id=1, enrollment_id=1, course_id=1, batch_id=1,
                             survey_type="completed", rating=9, created_at=when),
            catalog.BookDelivery(id=1, course_id=1, book_id=1, book_name="B", is_sent=1,
                                 sent_on=date(2026, 3, 5), is_deliverable=1,
                                 is_additional="IS NOT ADDITIONAL", generation_type=0,
                                 created_at=when),
            catalog.Book(id=1, name="B", sku="S", is_send_able=1),
            catalog.CourseBook(id=1, book_id=1, course_id=1, delivery_start_date=date(2022, 1, 1)),
            catalog.Bootcamp(id=1, name="Camp", refund_eligible_course=1),
            catalog.BootcampBook(id=1, book_id=1, bootcamp_id=1, delivery_start_date=date(2022, 1, 1)),
            staff.Staff(id=1, full_name="Asha", status=1, last_login=when),
            student_extras.EnrollmentPauseLog(id=1, enrollment_id=1, status="paused",
                                              request_source="student", paused_at=when,
                                              resumed_at=when, created_at=when),
            student_extras.StudentNote(id=1, student_id=1, created_at=when),
            student_extras.WeekDay(id=1, name="Monday"),
            student_extras.StudentAvailability(id=1, student_id=1, weekday_id=1,
                                               start_time=time(9), end_time=time(10)),
            Course(id=1, course_name="C", status=1, duration_days=1, course_type=1, is_job_eligible=0),
        ]
    )
    portal_db.commit()


CALLS = [
    ("search_feedback", {"kind": "assignment_csat"}),
    ("search_feedback", {"kind": "class_csat"}),
    ("search_feedback", {"kind": "nps"}),
    ("find_books", {"course_id": 1}),
    ("find_books", {"bootcamp_id": 1}),
    ("find_bootcamps", {}),
    ("search_book_deliveries", {}),
    ("find_staff", {}),
    ("search_pause_logs", {}),
    ("count_student_notes", {"student_id": 1}),
    ("get_student_availability", {"student_id": 1}),
    ("find_packages", {}),
]


@pytest.mark.parametrize("name,args", CALLS, ids=[f"{n}-{i}" for i, (n, _) in enumerate(CALLS)])
def test_tool_results_are_plain_json(seeded, name, args):
    result = ai_tools.dispatch(name, args)
    assert "error" not in result, result
    json.dumps(result)  # no default= : exactly what the SDK will do


def test_temporal_values_come_out_as_iso_strings(seeded):
    result = ai_tools.dispatch("find_books", {"course_id": 1})
    assert result["books"][0]["delivery_start_date"] == "2022-01-01"
    rows = ai_tools.dispatch("search_feedback", {"kind": "nps"})["responses"]
    assert rows[0]["created_at"] == "2026-03-04T05:06:07"
