"""Read-only queries over portal assignments and student assignments.

Same rules as `portal_service` and `enrollment_service`: SELECTs over
allow-listed columns only, every list capped at MAX_RESULTS, every value bound
as a parameter, and one shared filter builder per entity so a list and its
count can never disagree.

Two entities live here because they are two halves of one idea: `assignments`
is what a course sets, `student_assignments` is what one student was given.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, contains_eager

from app.models.portal.assignment import Assignment
from app.models.portal.codes import (
    ASSIGNMENT_STATUS_LABELS,
    ASSIGNMENT_TYPE_LABELS,
    STUDENT_ASSIGNMENT_STATUS_LABELS,
    StudentAssignmentStatus,
    label,
)
from app.models.portal.course import Course
from app.models.portal.enrollment import Enrollment
from app.models.portal.student_assignment import StudentAssignment
from app.models.portal.topic import Topic
from app.services.portal_service import MAX_RESULTS

# One enrollment's assignment list: a long course sets many, but a prompt
# still must not receive hundreds of rows.
MAX_PER_ENROLLMENT = 60

# The statuses that mean the student has actually handed something in.
# 0 and 1 are activation states -- the assignment is switched on but untouched
# -- so "submitted" must never be read as "not deactivated".
SUBMITTED_STATUSES = (
    StudentAssignmentStatus.SUBMITTED,
    StudentAssignmentStatus.RESUBMITTED,
    StudentAssignmentStatus.EVALUATED,
)


def _labelled(db: Session, statement: Select, labels: dict[int, str] | None) -> list[dict]:
    """Run a (value, count) statement and attach human labels to stored codes."""
    return [
        {"value": value, "count": count, **({"label": label(labels, value)} if labels else {})}
        for value, count in db.execute(statement)
    ]


# --------------------------------------------------------------------------- #
# assignments -- the definitions a course sets
# --------------------------------------------------------------------------- #
def build_assignment_filters(
    course_id: int | None = None,
    course_name: str | None = None,
    topic: str | None = None,
    assignment_code: str | None = None,
    assignment_type: int | None = None,
    status: int | None = None,
    plagiarism_checked: bool | None = None,
    min_exercises: int | None = None,
    max_exercises: int | None = None,
    created_after: date | None = None,
    created_before: date | None = None,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `course_name` and `topic` filter on joined tables, so every statement built
    from these must join Course and Topic -- the helpers below always do.
    """
    conditions: list[ColumnElement[bool]] = []

    if course_id is not None:
        conditions.append(Assignment.course_id == course_id)
    if course_name and course_name.strip():
        conditions.append(Course.course_name.like(f"%{course_name.strip()}%"))
    if topic and topic.strip():
        conditions.append(Topic.title.like(f"%{topic.strip()}%"))
    if assignment_code and assignment_code.strip():
        conditions.append(Assignment.assignment_code.like(f"%{assignment_code.strip()}%"))

    # 0 is a real assignment type (subjective), so this tests against None
    # rather than falsiness -- half the table would otherwise be unfilterable.
    if assignment_type is not None:
        conditions.append(Assignment.assignment_type == assignment_type)
    if status is not None:
        conditions.append(Assignment.status == status)

    if plagiarism_checked is True:
        conditions.append(Assignment.plagiarism == 1)
    elif plagiarism_checked is False:
        conditions.append(Assignment.plagiarism == 0)

    if min_exercises is not None:
        conditions.append(Assignment.number_of_exercises >= min_exercises)
    if max_exercises is not None:
        conditions.append(Assignment.number_of_exercises <= max_exercises)
    if created_after:
        conditions.append(Assignment.created_at >= created_after)
    if created_before:
        conditions.append(Assignment.created_at < created_before)

    return conditions


def _assignment_select(**criteria) -> Select:
    return (
        select(Assignment)
        .join(Assignment.course)
        .outerjoin(Assignment.topic)
        # contains_eager, not joinedload: the joins above are already there,
        # and joinedload would add a second aliased copy of each.
        .options(contains_eager(Assignment.course), contains_eager(Assignment.topic))
        .where(*build_assignment_filters(**criteria))
    )


def get_assignment(db: Session, assignment_id: int) -> Assignment:
    assignment = db.scalar(_assignment_select().where(Assignment.id == assignment_id))
    if assignment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Assignment not found"
        )
    return assignment


def _filters_on_topic(criteria: dict) -> bool:
    """`topic` is the only criterion that needs the topics table."""
    return criteria.get("topic") not in (None, "") and bool(str(criteria["topic"]).strip())


def _filters_on_course_name(criteria: dict) -> bool:
    """`course_name` is the only criterion that needs the courses table."""
    name = criteria.get("course_name")
    return name not in (None, "") and bool(str(name).strip())


def list_assignments(db: Session, limit: int = 10, **criteria) -> list[Assignment]:
    """Assignments matching every criterion, capped at MAX_RESULTS.

    Within one course they come in the order the course lists them. Across
    courses that order has no index behind it, so sorting the whole table took
    ~6s -- and, with other queries running, past the 15s statement cap. There
    the newest come first instead, which the primary key serves instantly.
    """
    if criteria.get("course_id") is not None:
        order = (Assignment.course_id, Assignment.ref_assignment_no, Assignment.id)
    else:
        order = (Assignment.id.desc(),)
    statement = (
        _assignment_select(**criteria).order_by(*order).limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement).unique())


def count_assignments(db: Session, **criteria) -> int:
    """True number of matches -- not the capped page size.

    Courses and topics are joined only when a criterion needs them. Neither
    join can change a count -- `assignments.course_id` is a NOT NULL foreign key
    (verified: no orphans) and the topics join is a LEFT JOIN on a unique key --
    but on 2.5M rows each one is expensive. Counting all assignments took 15.7s
    with both and 1.6s with neither; `status = 1` took 18s with the courses
    join and 5s without. The statement cap is 15s, so "how many assignments"
    failed outright.
    """
    statement = select(func.count(Assignment.id))
    if _filters_on_course_name(criteria):
        statement = statement.join(Assignment.course)
    if _filters_on_topic(criteria):
        statement = statement.outerjoin(Assignment.topic)
    return db.scalar(statement.where(*build_assignment_filters(**criteria))) or 0


_ASSIGNMENT_LABELLED = {
    "status": ASSIGNMENT_STATUS_LABELS,
    "type": ASSIGNMENT_TYPE_LABELS,
}


def breakdown_assignments(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Assignment counts grouped by one dimension.

    `by` is looked up in an allow-list, never used as a column name directly,
    and the check runs before any database access.
    """
    month = func.date_format(Assignment.created_at, "%Y-%m")
    dimensions = {
        "course": Course.course_name,
        "topic": Topic.title,
        "type": Assignment.assignment_type,
        "status": Assignment.status,
        "plagiarism": Assignment.plagiarism,
        "month": month,
    }
    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )

    field = dimensions[by]

    # Grouping by course or topic *name* means joining 2.5M rows to a names
    # table before grouping: over the 15s cap. Count per foreign-key id first
    # (an index scan: ~244 courses, ~47k topics), then attach names to that
    # small result and group again by name, so two topics sharing a title still
    # merge exactly as they did before. Only usable when no filter needs the
    # joined tables inside the count.
    if by in ("course", "topic") and not (
        _filters_on_course_name(criteria) or _filters_on_topic(criteria)
    ):
        fk = Assignment.course_id if by == "course" else Assignment.topic_id
        per_id = (
            select(fk.label("k"), func.count(Assignment.id).label("n"))
            .where(*build_assignment_filters(**criteria))
            .group_by(fk)
            .subquery()
        )
        total = func.sum(per_id.c.n)
        statement = select(field.label("value"), total.label("count")).select_from(per_id)
        # Both ids are NOT NULL foreign keys, so every group has a name; the
        # topics side stays an outer join only to match the joins used elsewhere.
        statement = (
            statement.join(Course, Course.id == per_id.c.k)
            if by == "course"
            else statement.outerjoin(Topic, Topic.id == per_id.c.k)
        )
        statement = (
            statement.group_by(field).order_by(total.desc()).limit(max(1, min(limit, MAX_RESULTS)))
        )
        return [{"value": value, "count": int(count)} for value, count in db.execute(statement)]

    # Months newest first, so a limit keeps the most recent ones; everything
    # else biggest group first.
    order = field.desc() if by == "month" else func.count(Assignment.id).desc()
    statement = select(field.label("value"), func.count(Assignment.id).label("count"))
    # Same reasoning as count_assignments: a join is paid for only by the
    # grouping or filter that needs it.
    if by == "course" or _filters_on_course_name(criteria):
        statement = statement.join(Assignment.course)
    if by == "topic" or _filters_on_topic(criteria):
        statement = statement.outerjoin(Assignment.topic)
    statement = (
        statement.where(*build_assignment_filters(**criteria))
        .group_by(field)
        .order_by(order)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return _labelled(db, statement, _ASSIGNMENT_LABELLED.get(by))


# --------------------------------------------------------------------------- #
# student_assignments -- what one student was given
# --------------------------------------------------------------------------- #
def _live() -> ColumnElement[bool]:
    """Soft-deleted rows never count."""
    return StudentAssignment.deleted_at.is_(None)


def build_student_assignment_filters(
    enrollment_id: int | None = None,
    student_id: int | None = None,
    assignment_id: int | None = None,
    course_id: int | None = None,
    course_name: str | None = None,
    status: int | None = None,
    submitted: bool | None = None,
    mandatory: bool | None = None,
    overdue: bool | None = None,
    due_after: date | None = None,
    due_before: date | None = None,
    min_submits: int | None = None,
    live: bool = True,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `student_id`, `course_id` and `course_name` reach through the enrollment,
    so every statement built from these joins Enrollment and Course.

    `live=False` leaves out the soft-delete predicate; only `_live_counts_by`
    uses it, and puts the predicate back by subtraction (see there).
    """
    conditions: list[ColumnElement[bool]] = [_live()] if live else []

    if enrollment_id is not None:
        conditions.append(StudentAssignment.enrollment_id == enrollment_id)
    if assignment_id is not None:
        conditions.append(StudentAssignment.assignment_id == assignment_id)
    # student_assignments has no student_id of its own; it hangs off the enrollment.
    if student_id is not None:
        conditions.append(Enrollment.student_id == student_id)
    if course_id is not None:
        conditions.append(Enrollment.course_id == course_id)
    if course_name and course_name.strip():
        conditions.append(Course.course_name.like(f"%{course_name.strip()}%"))

    if status is not None:
        conditions.append(StudentAssignment.status == status)

    # "Submitted" is a set of statuses, not "status is not 0": an assignment
    # merely switched on (status 1) has had nothing handed in. 4.35M of the
    # 4.0M+ rows sit at status 1, so getting this wrong would report almost
    # every assignment in the portal as submitted.
    if submitted is True:
        conditions.append(StudentAssignment.status.in_(SUBMITTED_STATUSES))
    elif submitted is False:
        conditions.append(StudentAssignment.status.notin_(SUBMITTED_STATUSES))

    if mandatory is True:
        conditions.append(StudentAssignment.mandatory == 1)
    elif mandatory is False:
        conditions.append(StudentAssignment.mandatory == 0)

    # Overdue means the deadline has passed with nothing handed in. A past
    # deadline on an evaluated assignment is simply history, not a problem.
    if overdue is True:
        conditions.append(StudentAssignment.submission_last_date < func.current_date())
        conditions.append(StudentAssignment.status.notin_(SUBMITTED_STATUSES))
    elif overdue is False:
        conditions.append(
            (StudentAssignment.submission_last_date >= func.current_date())
            | StudentAssignment.submission_last_date.is_(None)
            | StudentAssignment.status.in_(SUBMITTED_STATUSES)
        )

    if due_after:
        conditions.append(StudentAssignment.submission_last_date >= due_after)
    if due_before:
        conditions.append(StudentAssignment.submission_last_date < due_before)
    if min_submits is not None:
        conditions.append(StudentAssignment.submit_counter >= min_submits)

    return conditions


def _student_assignment_select(**criteria) -> Select:
    return (
        select(StudentAssignment)
        .join(StudentAssignment.enrollment)
        .join(Enrollment.course)
        .join(StudentAssignment.course_assignment)
        .outerjoin(Assignment.topic)
        .options(
            contains_eager(StudentAssignment.course_assignment).contains_eager(
                Assignment.topic
            ),
            contains_eager(StudentAssignment.enrollment).contains_eager(Enrollment.course),
        )
        .where(*build_student_assignment_filters(**criteria))
    )


def get_student_assignment(db: Session, student_assignment_id: int) -> StudentAssignment:
    row = db.scalar(
        _student_assignment_select().where(StudentAssignment.id == student_assignment_id)
    )
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Student assignment not found"
        )
    return row


def list_student_assignments(db: Session, limit: int = 10, **criteria) -> list[StudentAssignment]:
    """Matching student assignments, nearest deadline last."""
    statement = (
        _student_assignment_select(**criteria)
        .order_by(StudentAssignment.submission_last_date.desc(), StudentAssignment.id.desc())
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement).unique())


def enrollment_assignments(db: Session, enrollment_id: int) -> list[StudentAssignment]:
    """Every live assignment for one enrollment, in the order a student sees them."""
    statement = (
        _student_assignment_select(enrollment_id=enrollment_id)
        .order_by(Assignment.ref_assignment_no, StudentAssignment.id)
        .limit(MAX_PER_ENROLLMENT)
    )
    return list(db.scalars(statement).unique())


# Criteria that can only be answered by reaching into the enrollment. Anything
# else needs no join: grouping 4M rows by status should not drag two more
# tables along.
_NEEDS_ENROLLMENT = ("student_id", "course_id", "course_name")


def _joins_enrollment(criteria: dict) -> bool:
    return any(criteria.get(key) not in (None, "") for key in _NEEDS_ENROLLMENT)


def _scoped_student_assignments(
    statement: Select, *, enrollment: bool, assignment: bool = False
) -> Select:
    """Add only the joins this statement actually needs."""
    if enrollment:
        statement = statement.join(StudentAssignment.enrollment).join(Enrollment.course)
    if assignment:
        statement = statement.join(StudentAssignment.course_assignment).outerjoin(Assignment.topic)
    return statement


def count_student_assignments(db: Session, **criteria) -> int:
    """True number of matches -- not the capped page size.

    "All matching rows minus the soft-deleted ones" rather than a
    `deleted_at IS NULL` predicate, for the reason given in `_live_counts_by`.
    Measured: `mandatory = 1` took 15s+ (past the cap) the plain way and ~5s
    this way; `course_id = 1` swung between 6.7s and 15s+ and now stays well
    under, because counting through the enrollment index needs no row lookups.
    """
    matching = _scoped_student_assignments(
        select(func.count(StudentAssignment.id)), enrollment=_joins_enrollment(criteria)
    ).where(*build_student_assignment_filters(**criteria, live=False))
    deleted = matching.where(StudentAssignment.deleted_at.is_not(None))
    return (db.scalar(matching) or 0) - (db.scalar(deleted) or 0)


_STUDENT_ASSIGNMENT_LABELLED = {"status": STUDENT_ASSIGNMENT_STATUS_LABELS}


def _live_counts_by(field, criteria: dict, *, enrollment: bool = False):
    """Per-value counts of live rows, as a subquery with columns (k, n).

    Computed as "all rows" plus "minus the soft-deleted ones", not with
    `deleted_at IS NULL`. That predicate is nearly useless here -- one row in
    4.4M is soft-deleted -- but it forces a row lookup for every row the grouping
    touches: `GROUP BY status` took 27s with it and 2.4s without, because the
    `status` index alone can answer the grouping. The deleted rows are few and
    have their own index, so subtracting them is cheap and exact.

    `enrollment=True` adds the enrollment and course joins, for a student or
    course criterion (or grouping by course name); both branches get them.
    """
    conditions = build_student_assignment_filters(**criteria, live=False)
    everything = (
        _scoped_student_assignments(
            select(field.label("k"), func.count(StudentAssignment.id).label("n")),
            enrollment=enrollment,
        )
        .where(*conditions)
        .group_by(field)
    )
    removed = (
        _scoped_student_assignments(
            select(field.label("k"), (-func.count(StudentAssignment.id)).label("n")),
            enrollment=enrollment,
        )
        .where(*conditions, StudentAssignment.deleted_at.is_not(None))
        .group_by(field)
    )
    return everything.union_all(removed).subquery()


# Narrowing that lets a topic breakdown finish. Across the whole table it
# cannot: 4.4M rows point at 2.5M distinct assignments, and joining them to
# topics ran past 100s. A whole course is not narrow enough either -- course 1
# alone (~380k rows) still hit the 15s cap -- so only criteria that select a
# handful of rows count.
_NARROWS_TOPIC = ("enrollment_id", "student_id", "assignment_id")


def breakdown_student_assignments(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Student-assignment counts grouped by one allow-listed dimension.

    Four shapes, chosen so each stays under the portal's 15s statement cap on
    4.4M rows (measured on the live table):

    * status / mandatory / submit_counter / month -- one table, no joins:
      counted with `_live_counts_by` (2-8s; the plain form took 26s);
    * course -- counted per enrollment first, then names attached to that small
      result (4s; the plain three-table join did not finish in 120s);
    * any of the above with a student/course criterion -- the same subtraction,
      with the enrollment join added to both branches;
    * topic -- refused unless scoped to a student, enrollment or assignment,
      see `_NARROWS_TOPIC`.
    """
    month = func.date_format(StudentAssignment.created_at, "%Y-%m")
    dimensions = {
        "status": (StudentAssignment.status, False),
        "course": (Course.course_name, False),
        "mandatory": (StudentAssignment.mandatory, False),
        "submit_counter": (StudentAssignment.submit_counter, False),
        "topic": (Topic.title, True),
        "month": (month, False),
    }
    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )

    limit = max(1, min(limit, MAX_RESULTS))
    labels = _STUDENT_ASSIGNMENT_LABELLED.get(by)
    scoped_to_enrollment = _joins_enrollment(criteria)

    if by == "topic" and not any(criteria.get(key) not in (None, "") for key in _NARROWS_TOPIC):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                "Grouping every student assignment by topic is too large to run (4.4M rows). "
                "Narrow it with student_id or enrollment_id, or use breakdown_assignments "
                "by 'topic' to count assignment definitions per topic."
            ),
        )

    field, _ = dimensions[by]

    if by == "topic":
        # Scoped to a few rows (see above), so the plain joined form is fast.
        statement = (
            _scoped_student_assignments(
                select(field.label("value"), func.count(StudentAssignment.id).label("count")),
                enrollment=scoped_to_enrollment,
                assignment=True,
            )
            .where(*build_student_assignment_filters(**criteria))
            .group_by(field)
            .order_by(func.count(StudentAssignment.id).desc())
            .limit(limit)
        )
        return _labelled(db, statement, labels)

    if by == "course" and not scoped_to_enrollment:
        # Grouping 4.4M rows by course *name* means joining them all to names
        # first; count per enrollment (~54k) and attach names to that instead.
        per_enrollment = _live_counts_by(StudentAssignment.enrollment_id, criteria)
        total = func.sum(per_enrollment.c.n)
        statement = (
            select(Course.course_name.label("value"), total.label("count"))
            .select_from(per_enrollment)
            .join(Enrollment, Enrollment.id == per_enrollment.c.k)
            .join(Course, Course.id == Enrollment.course_id)
            .group_by(Course.course_name)
        )
        order = total.desc()
    else:
        counted = _live_counts_by(
            field, criteria, enrollment=scoped_to_enrollment or by == "course"
        )
        total = func.sum(counted.c.n)
        statement = (
            select(counted.c.k.label("value"), total.label("count"))
            .select_from(counted)
            .group_by(counted.c.k)
        )
        order = counted.c.k.desc() if by == "month" else total.desc()

    statement = statement.having(total > 0).order_by(order).limit(limit)
    return [
        {"value": value, "count": int(count), **({"label": label(labels, value)} if labels else {})}
        for value, count in db.execute(statement)
    ]
