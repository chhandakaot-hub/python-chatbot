"""Queries against the read-only portal database.

Every function here is a SELECT over allow-listed columns, bounded by an
explicit row limit. Nothing writes; the connection would refuse it anyway.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.models.portal.student import Student

# A chat question must never be able to pull the whole table into a prompt.
MAX_RESULTS = 25

# Portal Student.php constants: 0 = pending (~0.8k rows), 1 = active (~18.9k),
# 2 = disabled (a few dozen). See app/models/portal/codes.py.
KNOWN_STATUSES = {0, 1, 2}


def get_student(db: Session, student_id: int) -> Student:
    student = db.get(Student, student_id)
    if student is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Student not found"
        )
    return student


def get_student_by_email(db: Session, email: str) -> Student | None:
    return db.scalar(select(Student).where(Student.email == email))


def get_student_by_reg_code(db: Session, reg_code: str) -> Student | None:
    return db.scalar(select(Student).where(Student.reg_code == reg_code))


def build_filters(
    query: str | None = None,
    city: str | None = None,
    state: str | None = None,
    country: str | None = None,
    status: int | None = None,
    current_activity: str | None = None,
    registered_after: date | None = None,
    registered_before: date | None = None,
    last_login_after: date | None = None,
    never_logged_in: bool | None = None,
) -> list[ColumnElement[bool]]:
    """Turn optional criteria into SQL conditions, ANDed by the caller.

    Every value is bound as a parameter, never interpolated -- these are
    reachable from a chat box, so they are the obvious injection target.
    Shared by search and count so the two can never drift apart.
    """
    conditions: list[ColumnElement[bool]] = []

    if query and query.strip():
        pattern = f"%{query.strip()}%"
        conditions.append(
            or_(
                Student.full_name.like(pattern),
                Student.email.like(pattern),
                Student.reg_code.like(pattern),
            )
        )

    if city and city.strip():
        conditions.append(Student.city.like(f"%{city.strip()}%"))
    if state and state.strip():
        conditions.append(Student.state.like(f"%{state.strip()}%"))
    if country and country.strip():
        conditions.append(Student.country.like(f"%{country.strip()}%"))
    if current_activity and current_activity.strip():
        conditions.append(
            Student.what_are_you_doing_currently.like(f"%{current_activity.strip()}%")
        )

    if status is not None:
        conditions.append(Student.status == status)

    if registered_after:
        conditions.append(Student.created_at >= registered_after)
    if registered_before:
        conditions.append(Student.created_at < registered_before)
    if last_login_after:
        conditions.append(Student.last_login >= last_login_after)

    if never_logged_in is True:
        conditions.append(Student.last_login.is_(None))
    elif never_logged_in is False:
        conditions.append(Student.last_login.is_not(None))

    return conditions


def search_students(db: Session, limit: int = 10, **criteria) -> list[Student]:
    """Most recently registered students matching every criterion given.

    With no criteria this returns the newest `limit` students, which is still
    bounded by MAX_RESULTS -- there is no way to ask for the whole table.
    """
    statement = (
        select(Student)
        .where(*build_filters(**criteria))
        .order_by(Student.id.desc())
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement))


def count_students(db: Session, **criteria) -> int:
    """How many students match -- the true total, not the capped page size."""
    statement = select(func.count()).select_from(Student).where(*build_filters(**criteria))
    return db.scalar(statement) or 0


def group_students(db: Session, column: str, limit: int = 10, **criteria) -> list[dict]:
    """Counts per value of one allow-listed column, e.g. students per country."""
    groupable = {
        "country": Student.country,
        "state": Student.state,
        "city": Student.city,
        "status": Student.status,
        "current_activity": Student.what_are_you_doing_currently,
    }
    field = groupable.get(column)
    if field is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {column!r}. Allowed: {', '.join(sorted(groupable))}.",
        )

    statement = (
        select(field, func.count().label("count"))
        .where(*build_filters(**criteria))
        .group_by(field)
        .order_by(func.count().desc())
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return [{"value": value, "count": count} for value, count in db.execute(statement)]
