"""The assignments table holds ~2.5M rows and the portal connection kills any
statement over 15s. "How many assignments are active?" used to fail outright,
because every count joined `courses` and `topics` whether or not anything asked
for them: 15.7s for a plain count, 18s for `status = 1`.

These pin two things: which joins each query is allowed to make (the shape that
keeps it under the cap), and that the faster shapes return exactly what the
joined ones did.
"""

import pytest
from sqlalchemy import event

from app.models.portal.assignment import Assignment
from app.models.portal.course import Course
from app.models.portal.topic import Topic
from app.services import assignment_service as svc


@pytest.fixture
def sql(portal_db):
    """Every statement the service sends, lower-cased, in order."""
    seen: list[str] = []

    @event.listens_for(portal_db.get_bind(), "before_cursor_execute")
    def _record(conn, cursor, statement, parameters, context, executemany):
        seen.append(" ".join(statement.lower().split()))

    return seen


def _joins(statement: str, table: str) -> bool:
    return f"join {table} " in statement


# --------------------------------------------------------------------------- #
# Join shape
# --------------------------------------------------------------------------- #
def test_a_plain_count_joins_nothing(portal_db, sql):
    svc.count_assignments(portal_db)
    assert not _joins(sql[-1], "courses") and not _joins(sql[-1], "topics")


@pytest.mark.parametrize(
    "criteria",
    [{"status": 1}, {"assignment_type": 0}, {"plagiarism_checked": True}, {"course_id": 3}],
)
def test_filters_on_assignment_columns_join_nothing(portal_db, sql, criteria):
    svc.count_assignments(portal_db, **criteria)
    assert not _joins(sql[-1], "courses") and not _joins(sql[-1], "topics")


def test_a_topic_filter_joins_topics_only(portal_db, sql):
    svc.count_assignments(portal_db, topic="Indemnity")
    assert _joins(sql[-1], "topics") and not _joins(sql[-1], "courses")


def test_a_course_name_filter_joins_courses_only(portal_db, sql):
    svc.count_assignments(portal_db, course_name="Contract")
    assert _joins(sql[-1], "courses") and not _joins(sql[-1], "topics")


def test_blank_filters_do_not_trigger_a_join(portal_db, sql):
    svc.count_assignments(portal_db, topic="   ", course_name="")
    assert not _joins(sql[-1], "courses") and not _joins(sql[-1], "topics")


@pytest.mark.parametrize("by", ["status", "type", "plagiarism", "month"])
def test_breakdowns_by_assignment_columns_join_nothing(portal_db, sql, by):
    svc.breakdown_assignments(portal_db, by)
    assert not _joins(sql[-1], "courses") and not _joins(sql[-1], "topics")


@pytest.mark.parametrize("by,table", [("course", "courses"), ("topic", "topics")])
def test_grouping_by_name_counts_per_id_before_joining_names(portal_db, sql, by, table):
    """The join goes onto the small per-id result, not onto 2.5M rows."""
    svc.breakdown_assignments(portal_db, by)
    statement = sql[-1]
    assert _joins(statement, table)
    # the count sits in a subquery the names are joined onto
    assert statement.index("group by assignments.") < statement.index(f"join {table} ")


def test_grouping_by_name_with_a_matching_filter_falls_back_to_the_join(portal_db, sql):
    svc.breakdown_assignments(portal_db, "course", course_name="Contract")
    assert "group by courses.course_name" in sql[-1]


# --------------------------------------------------------------------------- #
# Order of a search
# --------------------------------------------------------------------------- #
def test_an_unfiltered_search_orders_by_the_primary_key(portal_db, sql):
    """Sorting the whole table by (course, ref no) has no index: ~6s."""
    svc.list_assignments(portal_db, 5)
    assert "order by assignments.id desc" in sql[-1]


def test_a_search_inside_one_course_keeps_the_courses_own_order(portal_db, sql):
    svc.list_assignments(portal_db, 5, course_id=1)
    assert "order by assignments.course_id, assignments.ref_assignment_no" in sql[-1]


# --------------------------------------------------------------------------- #
# The faster shapes return what the joined ones did
# --------------------------------------------------------------------------- #
def _assignment(i, course_id, topic_id, **kw):
    defaults = dict(
        id=i, course_id=course_id, topic_id=topic_id, assignment_code=f"A{i}",
        assignment_type=0, number_of_exercises=1, word_count=1, plagiarism=0, status=1,
        ref_assignment_no=i, is_bootcamp_written=0, auto_assignment=0,
    )
    return Assignment(**{**defaults, **kw})


@pytest.fixture
def seeded(portal_db):
    course = dict(status=1, duration_days=1, course_type=1, is_job_eligible=0)
    portal_db.add_all(
        [
            Course(id=1, course_name="Contract Drafting", **course),
            Course(id=2, course_name="Tax Law", **course),
            Topic(id=1, title="Indemnity"),
            Topic(id=2, title="Indemnity"),  # same title, different id: must merge
            Topic(id=3, title="Set-off"),
            _assignment(1, 1, 1),
            _assignment(2, 1, 2, status=0),
            _assignment(3, 1, 3, assignment_type=1, plagiarism=1),
            _assignment(4, 2, 1),
            _assignment(5, 2, 2),
        ]
    )
    portal_db.commit()
    return portal_db


def test_counts_are_right_without_the_joins(seeded):
    assert svc.count_assignments(seeded) == 5
    assert svc.count_assignments(seeded, status=0) == 1
    assert svc.count_assignments(seeded, assignment_type=1) == 1
    assert svc.count_assignments(seeded, plagiarism_checked=True) == 1
    assert svc.count_assignments(seeded, course_id=2) == 2
    assert svc.count_assignments(seeded, topic="Indemnity") == 4  # topic ids 1 and 2
    assert svc.count_assignments(seeded, course_name="Tax") == 2


def test_count_and_search_still_agree(seeded):
    for criteria in ({}, {"status": 1}, {"course_id": 1}, {"topic": "Set"}, {"course_name": "Tax"}):
        rows = svc.list_assignments(seeded, limit=25, **criteria)
        assert len(rows) == svc.count_assignments(seeded, **criteria), criteria


def test_group_by_course_matches_the_obvious_answer(seeded):
    groups = svc.breakdown_assignments(seeded, "course")
    assert groups == [
        {"value": "Contract Drafting", "count": 3},
        {"value": "Tax Law", "count": 2},
    ]


def test_group_by_topic_merges_topics_that_share_a_title(seeded):
    groups = {g["value"]: g["count"] for g in svc.breakdown_assignments(seeded, "topic")}
    assert groups == {"Indemnity": 4, "Set-off": 1}


def test_group_by_name_honours_other_filters(seeded):
    assert svc.breakdown_assignments(seeded, "course", status=0) == [
        {"value": "Contract Drafting", "count": 1}
    ]
    # course 2 holds one assignment on topic 1 and one on topic 2: both are
    # "Indemnity", so they merge into a single group of two.
    assert svc.breakdown_assignments(seeded, "topic", course_id=2) == [
        {"value": "Indemnity", "count": 2}
    ]


def test_group_by_name_respects_the_limit(seeded):
    assert len(svc.breakdown_assignments(seeded, "course", limit=1)) == 1


def test_labelled_breakdowns_keep_their_labels(seeded):
    by_status = {g["label"]: g["count"] for g in svc.breakdown_assignments(seeded, "status")}
    assert by_status == {"active": 4, "deactivated": 1}
    by_type = {g["label"]: g["count"] for g in svc.breakdown_assignments(seeded, "type")}
    assert by_type == {"subjective": 4, "written": 1}


def test_unfiltered_search_is_newest_first_and_a_course_search_is_in_course_order(seeded):
    assert [a.id for a in svc.list_assignments(seeded, 3)] == [5, 4, 3]
    assert [a.id for a in svc.list_assignments(seeded, 3, course_id=1)] == [1, 2, 3]


def test_a_bad_dimension_is_still_refused_before_any_query():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        svc.breakdown_assignments(None, "assignment_code")
    assert exc.value.status_code == 400
