"""Course completion -- whether a student has actually finished a course.

There is no `course_completions` table. Completion is a *verdict* the portal
computes from an enrollment, and the verdict is not the `completed` column:

    completed = 1                       -> passed the marks criteria
    + the course requires an LMS MCQ    -> not finished until mcq_completed = 1

That rule is the portal's own, in
Modules/CourseCompletionMaster/Http/Resources/CourseCompletionMasterResource.php
(`getCompleted`), and it matters at this scale: 53,910 enrollments carry
`lms_mcq = Y`, and 2,426 sit at `completed = 1, mcq_completed = 0`. Reporting
`completed = 1` as "completed" overstates the number of finished students by
those 2,426. `enrollment_service` exposes the raw flag, which is the right
thing for a question about the column; this module exposes the verdict, which
is the right thing for a question about students finishing.

Where the MCQ requirement comes from
------------------------------------
`enrollments.passing_criteria` -- a JSON snapshot of the criteria as they stood
when the student enrolled. The blob itself stays unmapped and is never selected
(it is bulky, and `tests/test_enrollments.py` pins that it stays off the
model); only the single `lms_mcq` key is read, server-side, by the expression
below. `course_criterias` would be the tidier source but is empty in practice
-- 2 rows for 215 courses -- so it would silently answer "no MCQ required" for
almost everything.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, Row, Select, and_, case, func, literal_column, or_, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.models.portal.codes import (
    COMPLETION_AWAITING_MCQ,
    COMPLETION_COMPLETED,
    COMPLETION_NOT_COMPLETED,
)
from app.models.portal.course import Course
from app.models.portal.enrollment import Enrollment
from app.models.portal.student_assignment import StudentAssignment
from app.services.assignment_service import SUBMITTED_STATUSES
from app.services.portal_service import MAX_RESULTS

# One student's courses. Small by nature; this only stops a pathological row
# count reaching a prompt.
MAX_PER_STUDENT = 50

# The one key read out of the unmapped passing_criteria blob. A literal with no
# interpolation: nothing a caller or the model supplies reaches this string.
_LMS_MCQ = literal_column(
    "JSON_UNQUOTE(JSON_EXTRACT(enrollments.passing_criteria, '$[0].lms_mcq'))"
)

# The portal writes this flag four different ways -- 'Y'/'N' and '1'/'0' --
# plus NULL for the 29,482 enrollments with no criteria snapshot at all.
# Anything that is not an affirmative means "no MCQ required", which is also
# what the portal falls back to when passing_criteria is missing.
MCQ_REQUIRED: ColumnElement[bool] = _LMS_MCQ.in_(("Y", "1"))

_PASSED_CRITERIA: ColumnElement[bool] = Enrollment.completed == 1
_MCQ_DONE: ColumnElement[bool] = func.coalesce(Enrollment.mcq_completed, 0) == 1

# The three states, as SQL. Kept here as one definition so the filter, the
# breakdown and the per-row label can never disagree about what "completed"
# means.
IS_COMPLETED: ColumnElement[bool] = and_(_PASSED_CRITERIA, or_(~MCQ_REQUIRED, _MCQ_DONE))
IS_AWAITING_MCQ: ColumnElement[bool] = and_(_PASSED_CRITERIA, MCQ_REQUIRED, ~_MCQ_DONE)
IS_NOT_COMPLETED: ColumnElement[bool] = func.coalesce(Enrollment.completed, 0) != 1

STATES = {
    "completed": IS_COMPLETED,
    "awaiting_mcq": IS_AWAITING_MCQ,
    "not_completed": IS_NOT_COMPLETED,
}

# The same three states as a value the database can group by.
STATE_CASE = case(
    (IS_AWAITING_MCQ, COMPLETION_AWAITING_MCQ),
    (IS_COMPLETED, COMPLETION_COMPLETED),
    else_=COMPLETION_NOT_COMPLETED,
)


def completion_state(completed: object, mcq_completed: object, mcq_required: object) -> str:
    """The same verdict in Python, for one already-loaded row.

    Mirrors STATE_CASE; `tests/test_completion.py` pins the two against each
    other so a change to one without the other is caught.
    """
    if int(completed or 0) != 1:
        return COMPLETION_NOT_COMPLETED
    if mcq_required and int(mcq_completed or 0) != 1:
        return COMPLETION_AWAITING_MCQ
    return COMPLETION_COMPLETED


def _live() -> ColumnElement[bool]:
    return Enrollment.deleted_at.is_(None)


def build_filters(
    student_id: int | None = None,
    course_id: int | None = None,
    course_name: str | None = None,
    batch_id: int | None = None,
    state: str | None = None,
    mcq_required: bool | None = None,
    is_certified: bool | None = None,
    min_percent: float | None = None,
    max_percent: float | None = None,
    completed_after: date | None = None,
    completed_before: date | None = None,
    certified_after: date | None = None,
    expiring_before: date | None = None,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `state` is the completion verdict, not the raw column: pass "completed",
    "awaiting_mcq" or "not_completed".
    """
    conditions: list[ColumnElement[bool]] = [_live()]

    if student_id is not None:
        conditions.append(Enrollment.student_id == student_id)
    if course_id is not None:
        conditions.append(Enrollment.course_id == course_id)
    if course_name and course_name.strip():
        conditions.append(Course.course_name.like(f"%{course_name.strip()}%"))
    if batch_id is not None:
        conditions.append(Enrollment.batch_id == batch_id)

    if state is not None:
        if state not in STATES:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown completion state {state!r}. "
                f"Allowed: {', '.join(sorted(STATES))}.",
            )
        conditions.append(STATES[state])

    if mcq_required is True:
        conditions.append(MCQ_REQUIRED)
    elif mcq_required is False:
        conditions.append(~MCQ_REQUIRED)

    if is_certified is True:
        conditions.append(Enrollment.is_certified == 1)
    elif is_certified is False:
        conditions.append(Enrollment.is_certified == 0)

    if min_percent is not None:
        conditions.append(Enrollment.current_percent >= min_percent)
    if max_percent is not None:
        conditions.append(Enrollment.current_percent <= max_percent)

    if completed_after:
        conditions.append(Enrollment.completed_at >= completed_after)
    if completed_before:
        conditions.append(Enrollment.completed_at < completed_before)
    if certified_after:
        conditions.append(Enrollment.certified_datetime >= certified_after)
    if expiring_before:
        conditions.append(Enrollment.course_expiry_date < expiring_before)

    return conditions


def _joins_course(criteria: dict) -> bool:
    """`course_name` is the only criterion that needs the courses table.

    Joining it regardless doubled the cost of every count over the 111k-row
    enrollments table for nothing.
    """
    return criteria.get("course_name") not in (None, "")


def _select(**criteria) -> Select:
    """Enrollments plus the derived MCQ requirement, which is not a column.

    Always joins: the schema reports the course name for every row.
    """
    return (
        select(Enrollment, MCQ_REQUIRED.label("mcq_required"))
        .join(Enrollment.course)
        # course is already joined above; batch is not, so it still needs a
        # join of its own.
        .options(contains_eager(Enrollment.course), joinedload(Enrollment.batch))
        .where(*build_filters(**criteria))
    )


def list_completions(db: Session, limit: int = 10, **criteria) -> list[Row]:
    """(Enrollment, mcq_required) rows, most recently completed first.

    Enrollments that never completed sort last, which is what a person scanning
    a completion list wants to see at the bottom.

    Done as a deferred join -- pick the ids first, then fetch them -- because
    `enrollments` is RANGE-partitioned by `created_at` and joining `courses` in
    the same statement makes the optimizer drive from `courses` and filesort
    every matching enrollment to find ten. Measured: 7.0s joined, 0.57s for the
    id query alone, 0.67s for the two steps together.
    """
    capped = max(1, min(limit, MAX_RESULTS))
    order = (Enrollment.completed_at.desc(), Enrollment.id.desc())

    ids = select(Enrollment.id)
    if _joins_course(criteria):
        ids = ids.join(Enrollment.course)
    ids = ids.where(*build_filters(**criteria)).order_by(*order).limit(capped)

    # Materialised rather than used as a bare IN (...): MySQL will not accept a
    # LIMIT inside an IN subquery.
    wanted = [row for row in db.scalars(ids)]
    if not wanted:
        return []

    statement = (
        select(Enrollment, MCQ_REQUIRED.label("mcq_required"))
        .join(Enrollment.course)
        .options(contains_eager(Enrollment.course), joinedload(Enrollment.batch))
        .where(Enrollment.id.in_(wanted))
        .order_by(*order)
    )
    return list(db.execute(statement).unique())


def student_completions(db: Session, student_id: int) -> list[Row]:
    """Completion state for every course one student is enrolled in."""
    statement = (
        _select(student_id=student_id)
        .order_by(Enrollment.created_at.desc())
        .limit(MAX_PER_STUDENT)
    )
    return list(db.execute(statement).unique())


def get_completion(db: Session, enrollment_id: int) -> Row:
    row = db.execute(_select().where(Enrollment.id == enrollment_id)).unique().first()
    if row is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Enrollment not found"
        )
    return row


def count_completions(db: Session, **criteria) -> int:
    """True number of matches -- not the capped page size."""
    statement = select(func.count(Enrollment.id))
    if _joins_course(criteria):
        statement = statement.join(Enrollment.course)
    return db.scalar(statement.where(*build_filters(**criteria))) or 0


def breakdown_completions(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Completion counts grouped by one dimension.

    `by` is looked up in an allow-list, never used as a column name directly,
    and the check runs before any database access.
    """
    month = func.date_format(Enrollment.completed_at, "%Y-%m")
    dimensions = {
        "state": STATE_CASE,
        "course": Course.course_name,
        "mcq_required": MCQ_REQUIRED,
        "is_certified": Enrollment.is_certified,
        "completed_month": month,
    }
    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )

    field = dimensions[by]
    order = field.desc() if by == "completed_month" else func.count(Enrollment.id).desc()
    statement = select(field.label("value"), func.count(Enrollment.id).label("count"))
    if by == "course" or _joins_course(criteria):
        statement = statement.join(Enrollment.course)
    statement = (
        statement.where(*build_filters(**criteria))
        .group_by(field)
        .order_by(order)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return [{"value": value, "count": count} for value, count in db.execute(statement)]


def summarise(db: Session, **criteria) -> dict[str, int]:
    """The three states in one query -- the answer to "how are they doing?".

    One pass over the matching rows rather than three counts, so the numbers
    are guaranteed to add up to the total.
    """
    statement = (
        select(
            func.count(Enrollment.id).label("total"),
            func.sum(case((IS_COMPLETED, 1), else_=0)).label("completed"),
            func.sum(case((IS_AWAITING_MCQ, 1), else_=0)).label("awaiting_mcq"),
            func.sum(case((IS_NOT_COMPLETED, 1), else_=0)).label("not_completed"),
            func.sum(case((Enrollment.is_certified == 1, 1), else_=0)).label("certified"),
        )
    )
    if _joins_course(criteria):
        statement = statement.join(Enrollment.course)
    row = db.execute(statement.where(*build_filters(**criteria))).one()
    return {
        "total": int(row.total or 0),
        "completed": int(row.completed or 0),
        "awaiting_mcq": int(row.awaiting_mcq or 0),
        "not_completed": int(row.not_completed or 0),
        "certified": int(row.certified or 0),
    }


def submission_progress(db: Session, enrollment_id: int) -> dict[str, int]:
    """How much of the coursework one enrollment has handed in.

    The portal's completion screen shows this next to the verdict, because
    "not completed" on its own does not say whether the student is one
    assignment short or has not started.
    """
    statement = select(
        func.count(StudentAssignment.id).label("assigned"),
        func.sum(case((StudentAssignment.status.in_(SUBMITTED_STATUSES), 1), else_=0)).label(
            "submitted"
        ),
        func.sum(case((StudentAssignment.mandatory == 1, 1), else_=0)).label("mandatory"),
    ).where(
        StudentAssignment.enrollment_id == enrollment_id,
        StudentAssignment.deleted_at.is_(None),
    )
    row = db.execute(statement).one()
    assigned = int(row.assigned or 0)
    submitted = int(row.submitted or 0)
    return {
        "assigned": assigned,
        "submitted": submitted,
        "mandatory": int(row.mandatory or 0),
        "outstanding": assigned - submitted,
    }
