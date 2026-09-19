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


def list_assignments(db: Session, limit: int = 10, **criteria) -> list[Assignment]:
    """Assignments matching every criterion, in the order a course lists them."""
    statement = (
        _assignment_select(**criteria)
        .order_by(Assignment.course_id, Assignment.ref_assignment_no, Assignment.id)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement).unique())


def count_assignments(db: Session, **criteria) -> int:
    """True number of matches -- not the capped page size."""
    statement = (
        select(func.count(Assignment.id))
        .join(Assignment.course)
        .outerjoin(Assignment.topic)
        .where(*build_assignment_filters(**criteria))
    )
    return db.scalar(statement) or 0


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
    # Months newest first, so a limit keeps the most recent ones; everything
    # else biggest group first.
    order = field.desc() if by == "month" else func.count(Assignment.id).desc()
    statement = (
        select(field.label("value"), func.count(Assignment.id).label("count"))
        .join(Assignment.course)
        .outerjoin(Assignment.topic)
        .where(*build_assignment_filters(**criteria))
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
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `student_id`, `course_id` and `course_name` reach through the enrollment,
    so every statement built from these joins Enrollment and Course.
    """
    conditions: list[ColumnElement[bool]] = [_live()]

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
    """True number of matches -- not the capped page size."""
    statement = _scoped_student_assignments(
        select(func.count(StudentAssignment.id)), enrollment=_joins_enrollment(criteria)
    ).where(*build_student_assignment_filters(**criteria))
    return db.scalar(statement) or 0


_STUDENT_ASSIGNMENT_LABELLED = {"status": STUDENT_ASSIGNMENT_STATUS_LABELS}


def breakdown_student_assignments(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Student-assignment counts grouped by one allow-listed dimension."""
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

    field, needs_assignment = dimensions[by]
    statement = _scoped_student_assignments(
        select(field.label("value"), func.count(StudentAssignment.id).label("count")),
        enrollment=(by == "course") or _joins_enrollment(criteria),
        assignment=needs_assignment,
    )

    order = field.desc() if by == "month" else func.count(StudentAssignment.id).desc()
    statement = (
        statement.where(*build_student_assignment_filters(**criteria))
        .group_by(field)
        .order_by(order)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return _labelled(db, statement, _STUDENT_ASSIGNMENT_LABELLED.get(by))
