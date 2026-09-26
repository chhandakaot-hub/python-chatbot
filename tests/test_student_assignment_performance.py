"""Student-assignment breakdowns on a 4.4M-row table under a 15s statement cap.

`student_assignments` holds 4.4M rows and exactly one is soft-deleted. The plain
`WHERE deleted_at IS NULL GROUP BY status` took 27s, because that predicate
forces a row lookup for every row grouped; the same grouping without it takes
2s. By course, the three-table join did not finish in 120s. These pin the query
shapes that fixed that, and that the fast shapes give exactly the answers the
plain ones did.
"""

from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.models.portal.assignment import Assignment
from app.models.portal.course import Course
from app.models.portal.enrollment import Enrollment
from app.models.portal.student_assignment import StudentAssignment
from app.models.portal.topic import Topic
from app.services import ai_tools, assignment_service as svc


@pytest.fixture
def sql(portal_db):
    seen: list[str] = []

    @event.listens_for(portal_db.get_bind(), "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.lower().split()))

    return seen


def _joins(statement: str, table: str) -> bool:
    return f"join {table} " in statement


# --------------------------------------------------------------------------- #
# Query shape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("by", ["status", "mandatory", "submit_counter", "month"])
def test_single_table_breakdowns_join_nothing_and_subtract_the_deleted(portal_db, sql, by):
    svc.breakdown_student_assignments(portal_db, by)
    statement = sql[-1]
    assert not _joins(statement, "enrollments") and not _joins(statement, "courses")
    assert "union all" in statement
    # the deleted rows are handled by their own branch, not by a predicate on
    # every grouped row
    assert statement.count("deleted_at is not null") == 1
    assert "deleted_at is null" not in statement


def test_by_course_counts_per_enrollment_before_joining_names(portal_db, sql):
    svc.breakdown_student_assignments(portal_db, "course")
    statement = sql[-1]
    assert statement.index("group by student_assignments.enrollment_id") < statement.index(
        "join enrollments "
    )
    assert _joins(statement, "courses")


@pytest.mark.parametrize("criteria", [{"student_id": 5}, {"course_id": 1}, {"course_name": "x"}])
def test_an_enrollment_criterion_adds_the_join_to_both_branches(portal_db, sql, criteria):
    """The criterion needs the enrollment join, but the soft-delete predicate is
    still handled by subtraction: `course_id = 1` on the plain form swung between
    6.7s and 15s+ (the cap)."""
    svc.breakdown_student_assignments(portal_db, "status", **criteria)
    statement = sql[-1]
    assert statement.count("join enrollments ") == 2  # everything + removed
    assert "union all" in statement and "deleted_at is null" not in statement


@pytest.mark.parametrize(
    "criteria", [{}, {"mandatory": True}, {"status": 5}, {"student_id": 5}, {"course_id": 1}]
)
def test_counts_subtract_the_deleted_rows_instead_of_filtering_every_row(portal_db, sql, criteria):
    svc.count_student_assignments(portal_db, **criteria)
    everything, removed = sql[-2], sql[-1]
    assert "deleted_at" not in everything
    assert "deleted_at is not null" in removed


def test_a_plain_count_joins_nothing_and_a_student_count_joins_the_enrollment(portal_db, sql):
    svc.count_student_assignments(portal_db, mandatory=True)
    assert not _joins(sql[-2], "enrollments") and not _joins(sql[-1], "enrollments")
    svc.count_student_assignments(portal_db, student_id=5)
    assert _joins(sql[-2], "enrollments") and _joins(sql[-1], "enrollments")


def test_a_topic_breakdown_of_the_whole_table_is_refused_before_any_query():
    with pytest.raises(HTTPException) as exc:
        svc.breakdown_student_assignments(None, "topic")  # db=None: must not be touched
    assert exc.value.status_code == 400
    assert "too large" in exc.value.detail and "student_id" in exc.value.detail


@pytest.mark.parametrize("criteria", [{"course_id": 1}, {"status": 5}, {"course_name": "x"}])
def test_a_course_is_not_narrow_enough_for_a_topic_breakdown(criteria):
    """Course 1 alone (~380k rows) still hit the 15s cap when measured."""
    with pytest.raises(HTTPException):
        svc.breakdown_student_assignments(None, "topic", **criteria)


@pytest.mark.parametrize("criteria", [{"student_id": 1}, {"enrollment_id": 1}, {"assignment_id": 1}])
def test_a_topic_breakdown_scoped_to_a_few_rows_is_allowed(portal_db, criteria):
    assert svc.breakdown_student_assignments(portal_db, "topic", **criteria) == []


def test_a_bad_dimension_is_still_refused_before_any_query():
    with pytest.raises(HTTPException) as exc:
        svc.breakdown_student_assignments(None, "status_code")
    assert exc.value.status_code == 400


def test_the_live_predicate_can_only_be_dropped_deliberately():
    """`live` defaults on; every ordinary caller keeps the soft-delete filter."""
    assert "deleted_at IS NULL" in " ".join(str(c) for c in svc.build_student_assignment_filters())
    assert "deleted_at" not in " ".join(
        str(c) for c in svc.build_student_assignment_filters(live=False)
    )


# --------------------------------------------------------------------------- #
# The fast shapes return what the plain ones did
# --------------------------------------------------------------------------- #
def _sa(i, enrollment_id, **kw):
    defaults = dict(
        id=i, enrollment_id=enrollment_id, assignment_id=1, submit_counter=1, status=1,
        number_of_exercises=1, mandatory=0, created_at=datetime(2026, 1, 5),
    )
    return StudentAssignment(**{**defaults, **kw})


@pytest.fixture
def seeded(portal_db):
    course = dict(status=1, duration_days=1, course_type=1, is_job_eligible=0)
    enrollment = dict(status=1, current_percent=0, is_certified=0, created_at=datetime(2026, 1, 1))
    portal_db.add_all(
        [
            Course(id=1, course_name="Contract Drafting", **course),
            Course(id=2, course_name="Tax Law", **course),
            Topic(id=1, title="Indemnity"),
            Assignment(id=1, course_id=1, topic_id=1, assignment_code="A", assignment_type=0,
                       number_of_exercises=1, word_count=1, plagiarism=0, status=1,
                       is_bootcamp_written=0, auto_assignment=0),
            Enrollment(id=1, student_id=10, course_id=1, **enrollment),
            Enrollment(id=2, student_id=11, course_id=2, **enrollment),
            Enrollment(id=3, student_id=10, course_id=2, **enrollment),
            # enrollment 1 (Contract Drafting): three live, one soft-deleted
            _sa(1, 1, status=1, mandatory=1),
            _sa(2, 1, status=1),
            _sa(3, 1, status=5, submit_counter=3, created_at=datetime(2026, 2, 5)),
            _sa(4, 1, status=5, deleted_at=datetime(2026, 3, 1)),
            # enrollment 2 (Tax Law): two live
            _sa(5, 2, status=3, mandatory=1, submit_counter=2, created_at=datetime(2026, 2, 9)),
            _sa(6, 2, status=1),
            # enrollment 3 (Tax Law): only ever a deleted row -- must vanish, not show 0
            _sa(7, 3, status=4, submit_counter=9, deleted_at=datetime(2026, 3, 2)),
        ]
    )
    portal_db.commit()
    return portal_db


def _values(groups):
    return {g["value"]: g["count"] for g in groups}


def test_by_status_counts_live_rows_only_with_labels(seeded):
    groups = svc.breakdown_student_assignments(seeded, "status")
    assert _values(groups) == {1: 3, 5: 1, 3: 1}  # the deleted 5 and the deleted 4 are gone
    assert {g["label"] for g in groups} == {"active (not submitted)", "evaluated", "submitted"}
    assert groups[0]["value"] == 1  # biggest group first


def test_a_value_whose_every_row_is_deleted_does_not_appear_as_zero(seeded):
    assert 4 not in _values(svc.breakdown_student_assignments(seeded, "status"))
    assert 9 not in _values(svc.breakdown_student_assignments(seeded, "submit_counter"))


def test_by_mandatory_and_submit_counter(seeded):
    # 5 live rows (ids 1,2,3,5,6): two mandatory, three not
    assert _values(svc.breakdown_student_assignments(seeded, "mandatory")) == {0: 3, 1: 2}
    assert _values(svc.breakdown_student_assignments(seeded, "submit_counter")) == {
        1: 3, 2: 1, 3: 1,
    }


def test_by_month_is_newest_first(seeded):
    groups = svc.breakdown_student_assignments(seeded, "month")
    assert [(g["value"], g["count"]) for g in groups] == [("2026-02", 2), ("2026-01", 3)]


def test_by_course_names_the_courses_and_counts_live_rows(seeded):
    groups = svc.breakdown_student_assignments(seeded, "course")
    assert [(g["value"], g["count"]) for g in groups] == [
        ("Contract Drafting", 3),
        ("Tax Law", 2),
    ]  # enrollment 3 (all deleted) does not add a row to Tax Law


def test_other_criteria_apply_on_the_fast_paths(seeded):
    assert _values(svc.breakdown_student_assignments(seeded, "status", submitted=False)) == {1: 3}
    assert _values(svc.breakdown_student_assignments(seeded, "status", mandatory=True)) == {1: 1, 3: 1}
    by_course = svc.breakdown_student_assignments(seeded, "course", status=1)
    assert [(g["value"], g["count"]) for g in by_course] == [("Contract Drafting", 2), ("Tax Law", 1)]


def test_the_plain_path_agrees_with_the_fast_path(seeded):
    """An enrollment criterion takes the plain query; the numbers must match."""
    fast = _values(svc.breakdown_student_assignments(seeded, "status", enrollment_id=None))
    scoped_total = svc.count_student_assignments(seeded)
    assert sum(fast.values()) == scoped_total == 5
    plain = svc.breakdown_student_assignments(seeded, "status", course_id=2)
    assert _values(plain) == {3: 1, 1: 1}


def test_counts_exclude_deleted_rows_with_and_without_an_enrollment_criterion(seeded):
    assert svc.count_student_assignments(seeded) == 5  # 7 rows, 2 soft-deleted
    assert svc.count_student_assignments(seeded, mandatory=True) == 2
    assert svc.count_student_assignments(seeded, mandatory=False) == 3
    assert svc.count_student_assignments(seeded, status=5) == 1  # the deleted 5 is not counted
    assert svc.count_student_assignments(seeded, submitted=True) == 2
    assert svc.count_student_assignments(seeded, student_id=10) == 3  # enrollments 1 and 3
    assert svc.count_student_assignments(seeded, course_id=2) == 2  # enrollments 2 and 3
    assert svc.count_student_assignments(seeded, course_id=2, status=3) == 1
    assert svc.count_student_assignments(seeded, course_name="Tax") == 2
    assert svc.count_student_assignments(seeded, student_id=999) == 0


def test_a_count_and_its_list_never_disagree(seeded):
    for criteria in ({}, {"status": 1}, {"mandatory": True}, {"course_id": 1}, {"student_id": 10}):
        listed = svc.list_student_assignments(seeded, limit=25, **criteria)
        assert len(listed) == svc.count_student_assignments(seeded, **criteria), criteria


def test_a_breakdown_always_sums_to_its_count(seeded):
    for by in ("status", "mandatory", "submit_counter", "month", "course"):
        for criteria in ({}, {"student_id": 10}, {"course_id": 2}, {"mandatory": True}):
            total = sum(g["count"] for g in svc.breakdown_student_assignments(
                seeded, by, limit=25, **criteria))
            assert total == svc.count_student_assignments(seeded, **criteria), (by, criteria)


def test_breakdowns_scoped_to_an_enrollment_criterion_are_right(seeded):
    assert _values(svc.breakdown_student_assignments(seeded, "status", student_id=10)) == {1: 2, 5: 1}
    assert _values(svc.breakdown_student_assignments(seeded, "status", course_id=2)) == {3: 1, 1: 1}
    by_course = svc.breakdown_student_assignments(seeded, "course", student_id=10)
    assert [(g["value"], g["count"]) for g in by_course] == [("Contract Drafting", 3)]


def test_limit_is_applied(seeded):
    assert len(svc.breakdown_student_assignments(seeded, "status", limit=1)) == 1
    assert len(svc.breakdown_student_assignments(seeded, "course", limit=1)) == 1


def test_topic_breakdown_works_when_scoped(seeded):
    groups = svc.breakdown_student_assignments(seeded, "topic", student_id=10)
    assert groups == [{"value": "Indemnity", "count": 3}]


# --------------------------------------------------------------------------- #
# What the model is told when a query is stopped
# --------------------------------------------------------------------------- #
def test_a_statement_timeout_tells_the_model_not_to_retry(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def _stopped(**_):
        raise OperationalError("SELECT ...", {}, Exception(1969, "max_statement_time exceeded"))

    monkeypatch.setitem(ai_tools._HANDLERS, "count_students", _stopped)
    result = ai_tools.dispatch("count_students", {})
    assert "Do not retry" in result["error"]
    assert "15s" in result["error"]


def test_a_timeout_is_a_warning_not_a_traceback(monkeypatch, caplog):
    from sqlalchemy.exc import OperationalError

    def _stopped(**_):
        raise OperationalError("SELECT ...", {}, Exception(1969, "max_statement_time exceeded"))

    monkeypatch.setitem(ai_tools._HANDLERS, "count_students", _stopped)
    with caplog.at_level("WARNING", logger=ai_tools.logger.name):
        ai_tools.dispatch("count_students", {})
    record = next(r for r in caplog.records if "statement cap" in r.getMessage())
    assert record.levelname == "WARNING" and record.exc_info is None


def test_any_other_database_error_is_still_reported_as_a_failure(monkeypatch):
    from sqlalchemy.exc import OperationalError

    def _broken(**_):
        raise OperationalError("SELECT ...", {}, Exception(2013, "lost connection"))

    monkeypatch.setitem(ai_tools._HANDLERS, "count_students", _broken)
    assert ai_tools.dispatch("count_students", {}) == {"error": "Tool count_students failed."}


def test_the_topic_refusal_reaches_the_model_as_data():
    result = ai_tools.dispatch("breakdown_student_assignments", {"by": "topic"})
    assert "too large" in result["error"]
