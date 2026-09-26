"""Read-only queries over portal enrollments.

Same rules as `portal_service`: SELECTs over allow-listed columns only, every
list capped at MAX_RESULTS, every value bound as a parameter, and one shared
filter builder so a list and its count can never disagree.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, contains_eager, joinedload

from app.models.portal.codes import (
    ENROLLMENT_STATUS_LABELS,
    ENROLLMENT_TYPE_LABELS,
    EnrollmentStatus,
    label,
)
from app.models.portal.course import Course
from app.models.portal.course_batch import CourseBatch
from app.models.portal.enrollment import Enrollment
from app.services.portal_service import MAX_RESULTS

# A single student rarely has more than a handful; this only stops a
# pathological row count reaching a prompt.
MAX_PER_STUDENT = 50


def _live() -> ColumnElement[bool]:
    """Soft-deleted rows never count. None exist today; that can change."""
    return Enrollment.deleted_at.is_(None)


def build_filters(
    student_id: int | None = None,
    course_id: int | None = None,
    course_name: str | None = None,
    batch_id: int | None = None,
    status: int | None = None,
    enrollment_type: int | None = None,
    completed: bool | None = None,
    is_certified: bool | None = None,
    paused: bool | None = None,
    enrolled_after: date | None = None,
    enrolled_before: date | None = None,
    expiring_before: date | None = None,
    min_progress: float | None = None,
    max_progress: float | None = None,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `course_name` filters on the joined `courses` table, so every statement
    built from these must join Course -- `_select()` and `count_enrollments()`
    always do.
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
    if status is not None:
        conditions.append(Enrollment.status == status)
    if enrollment_type is not None:
        # The column is a varchar holding "1".."4".
        conditions.append(Enrollment.type == str(int(enrollment_type)))

    if completed is True:
        conditions.append(Enrollment.completed == 1)
    elif completed is False:
        conditions.append(func.coalesce(Enrollment.completed, 0) == 0)

    if is_certified is True:
        conditions.append(Enrollment.is_certified == 1)
    elif is_certified is False:
        conditions.append(Enrollment.is_certified == 0)

    # `status` is the enrollment's current state. `pause_status` is a history
    # of pause requests -- a "resumed" enrollment keeps it -- so it must not be
    # used to decide whether something is paused now.
    if paused is True:
        conditions.append(Enrollment.status == EnrollmentStatus.PAUSED)
    elif paused is False:
        conditions.append(Enrollment.status != EnrollmentStatus.PAUSED)

    if enrolled_after:
        conditions.append(Enrollment.created_at >= enrolled_after)
    if enrolled_before:
        conditions.append(Enrollment.created_at < enrolled_before)
    if expiring_before:
        conditions.append(Enrollment.course_expiry_date < expiring_before)

    if min_progress is not None:
        conditions.append(Enrollment.current_percent >= min_progress)
    if max_progress is not None:
        conditions.append(Enrollment.current_percent <= max_progress)

    return conditions


def _select(**criteria) -> Select:
    return (
        select(Enrollment)
        .join(Enrollment.course)
        # course is already joined above; batch is not.
        .options(contains_eager(Enrollment.course), joinedload(Enrollment.batch))
        .where(*build_filters(**criteria))
    )


def get_enrollment(db: Session, enrollment_id: int) -> Enrollment:
    enrollment = db.scalar(_select().where(Enrollment.id == enrollment_id))
    if enrollment is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Enrollment not found"
        )
    return enrollment


def list_enrollments(db: Session, limit: int = 10, **criteria) -> list[Enrollment]:
    """Newest enrollments matching every criterion, capped at MAX_RESULTS."""
    statement = (
        _select(**criteria)
        .order_by(Enrollment.created_at.desc())
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement).unique())


def student_enrollments(db: Session, student_id: int) -> list[Enrollment]:
    """Every live enrollment for one student, newest first."""
    statement = (
        _select(student_id=student_id)
        .order_by(Enrollment.created_at.desc())
        .limit(MAX_PER_STUDENT)
    )
    return list(db.scalars(statement).unique())


def _joins_course(criteria: dict) -> bool:
    """`course_name` is the only criterion that needs the courses table."""
    return criteria.get("course_name") not in (None, "")


def count_enrollments(db: Session, **criteria) -> int:
    """True number of matches -- not the capped page size."""
    statement = select(func.count(Enrollment.id))
    # Joining courses regardless made every count pay for it: counting active
    # enrollments took nine seconds where it now takes well under one.
    if _joins_course(criteria):
        statement = statement.join(Enrollment.course)
    return db.scalar(statement.where(*build_filters(**criteria))) or 0


# Dimensions whose stored codes get a human label in breakdown results.
_LABELLED = {"status": ENROLLMENT_STATUS_LABELS, "type": ENROLLMENT_TYPE_LABELS}


def breakdown_enrollments(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Enrollment counts grouped by one dimension.

    `by` is looked up in an allow-list, never used as a column name directly,
    and the check runs before any database access.
    """
    month = func.date_format(Enrollment.created_at, "%Y-%m")
    dimensions = {
        "course": (Course.course_name, False),
        "status": (Enrollment.status, False),
        "type": (Enrollment.type, False),
        "completed": (func.coalesce(Enrollment.completed, 0), False),
        "is_certified": (Enrollment.is_certified, False),
        "batch": (CourseBatch.batch_date, True),
        "month": (month, False),
    }
    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )

    field, needs_batch = dimensions[by]
    statement = select(field.label("value"), func.count(Enrollment.id).label("count")).join(
        Enrollment.course
    )
    if needs_batch:
        statement = statement.outerjoin(Enrollment.batch)

    # Months newest first, so a limit keeps the most recent ones; everything
    # else biggest group first.
    order = field.desc() if by == "month" else func.count(Enrollment.id).desc()
    statement = (
        statement.where(*build_filters(**criteria))
        .group_by(field)
        .order_by(order)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )

    labels = _LABELLED.get(by)
    return [
        {"value": value, "count": count, **({"label": label(labels, value)} if labels else {})}
        for value, count in db.execute(statement)
    ]


def _course_query(*columns, name: str, status: int | None = None) -> Select:
    """Shared by the list and its count, so the two cannot disagree."""
    statement = (
        select(*columns)
        .select_from(Course)
        .where(Course.deleted_at.is_(None), Course.course_name.like(f"%{name.strip()}%"))
    )
    if status is not None:
        statement = statement.where(Course.status == status)  # 0 pending, 1 active
    return statement


def count_courses(db: Session, name: str, status: int | None = None) -> int:
    """True number of courses matching -- not the capped list length."""
    return db.scalar(_course_query(func.count(Course.id), name=name, status=status)) or 0


def find_courses(
    db: Session, name: str, limit: int = 10, status: int | None = None
) -> list[Course]:
    """Courses by partial name -- lets the model resolve a name to an id.

    At most MAX_RESULTS; `count_courses` says how many matched.
    """
    statement = (
        _course_query(Course, name=name, status=status)
        .order_by(Course.course_name)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement))
