"""Read-only queries over portal staff, their roles and their course assignments.

Staff are identified by name and role only: no email, phone or credentials are
mapped (see `models/portal/staff.py`). A soft-deleted account never counts.
"""

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.portal.codes import USER_STATUS_LABELS, label
from app.models.portal.course import Course
from app.models.portal.staff import (
    STAFF_MODEL_TYPE,
    CourseEvaluator,
    CourseInstructor,
    CourseMentor,
    Role,
    Staff,
    StaffRole,
)
from app.services.portal_service import MAX_RESULTS

# One person holds a handful of roles at most; this only bounds a bad row count.
MAX_PER_PERSON = 50


def _cap(limit: int) -> int:
    return max(1, min(limit, MAX_RESULTS))


def build_filters(
    query: str | None = None,
    role: str | None = None,
    status: int | None = None,
    never_logged_in: bool | None = None,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `role` filters through a subquery, so no statement built from these needs a
    join to the role tables.
    """
    conditions: list[ColumnElement[bool]] = [Staff.deleted_at.is_(None)]
    if query and query.strip():
        conditions.append(Staff.full_name.like(f"%{query.strip()}%"))
    if status is not None:
        conditions.append(Staff.status == status)
    if role and role.strip():
        conditions.append(
            Staff.id.in_(
                select(StaffRole.model_id)
                .join(Role, Role.id == StaffRole.role_id)
                .where(
                    StaffRole.model_type == STAFF_MODEL_TYPE,
                    Role.deleted_at.is_(None),
                    Role.name.like(f"%{role.strip()}%"),
                )
            )
        )
    if never_logged_in is True:
        conditions.append(Staff.last_login.is_(None))
    elif never_logged_in is False:
        conditions.append(Staff.last_login.is_not(None))
    return conditions


def _roles_for(db: Session, staff_ids: list[int]) -> dict[int, list[str]]:
    if not staff_ids:
        return {}
    rows = db.execute(
        select(StaffRole.model_id, Role.name)
        .join(Role, Role.id == StaffRole.role_id)
        .where(
            StaffRole.model_type == STAFF_MODEL_TYPE,
            StaffRole.model_id.in_(staff_ids),
            Role.deleted_at.is_(None),
        )
        .order_by(Role.name)
    )
    out: dict[int, list[str]] = {}
    for staff_id, name in rows:
        out.setdefault(staff_id, []).append(name)
    return out


def find_staff(db: Session, limit: int = 10, **criteria) -> list[dict]:
    """Staff by name, role or status, each with their roles."""
    people = list(
        db.scalars(
            select(Staff)
            .where(*build_filters(**criteria))
            .order_by(Staff.full_name)
            .limit(_cap(limit))
        )
    )
    roles = _roles_for(db, [p.id for p in people])
    return [
        {
            "id": p.id,
            "full_name": p.full_name.strip(),
            "status": p.status,
            "status_label": label(USER_STATUS_LABELS, p.status),
            "roles": roles.get(p.id, []),
            "last_login": p.last_login,
        }
        for p in people
    ]


def count_staff(db: Session, **criteria) -> int:
    return db.scalar(select(func.count(Staff.id)).where(*build_filters(**criteria))) or 0


def breakdown_staff(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Staff counts grouped by 'role' or 'status'."""
    if by not in {"role", "status"}:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: role, status.",
        )
    conditions = build_filters(**criteria)
    if by == "status":
        statement = (
            select(Staff.status.label("value"), func.count(Staff.id).label("count"))
            .where(*conditions)
            .group_by(Staff.status)
            .order_by(func.count(Staff.id).desc())
            .limit(_cap(limit))
        )
        return [
            {"value": v, "count": n, "label": label(USER_STATUS_LABELS, v)}
            for v, n in db.execute(statement)
        ]
    statement = (
        select(Role.name.label("value"), func.count(Staff.id).label("count"))
        .select_from(Staff)
        .join(StaffRole, StaffRole.model_id == Staff.id)
        .join(Role, Role.id == StaffRole.role_id)
        .where(StaffRole.model_type == STAFF_MODEL_TYPE, Role.deleted_at.is_(None), *conditions)
        .group_by(Role.name)
        .order_by(func.count(Staff.id).desc())
        .limit(_cap(limit))
    )
    return [{"value": v, "count": n} for v, n in db.execute(statement)]


def course_staff(db: Session, course_id: int) -> dict:
    """The evaluators, instructors and mentors attached to one course."""
    course = db.scalar(
        select(Course).where(Course.id == course_id, Course.deleted_at.is_(None))
    )
    if course is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Course not found")

    def people(link, person_col, *extra) -> list[dict]:
        rows = db.execute(
            select(Staff.id, Staff.full_name)
            .join(link, person_col == Staff.id)
            .where(link.course_id == course_id, Staff.deleted_at.is_(None), *extra)
            .order_by(Staff.full_name)
            .limit(MAX_PER_PERSON)
        )
        return [{"id": i, "full_name": n.strip()} for i, n in rows]

    return {
        "course_id": course.id,
        "course_name": course.course_name,
        "evaluators": people(CourseEvaluator, CourseEvaluator.evaluator_id),
        "instructors": people(
            CourseInstructor, CourseInstructor.instructor_id, CourseInstructor.deleted_at.is_(None)
        ),
        "mentors": people(CourseMentor, CourseMentor.mentor_id),
    }


def staff_courses(db: Session, staff_id: int) -> dict:
    """The courses one staff member evaluates, instructs or mentors."""
    person = db.scalar(select(Staff).where(Staff.id == staff_id, Staff.deleted_at.is_(None)))
    if person is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Staff not found")

    def courses(link, person_col, *extra) -> list[dict]:
        rows = db.execute(
            select(Course.id, Course.course_name)
            .join(link, link.course_id == Course.id)
            .where(person_col == staff_id, Course.deleted_at.is_(None), *extra)
            .order_by(Course.course_name)
            .limit(MAX_PER_PERSON)
        )
        return [{"course_id": i, "course_name": n} for i, n in rows]

    return {
        "staff_id": person.id,
        "full_name": person.full_name.strip(),
        "roles": _roles_for(db, [person.id]).get(person.id, []),
        "evaluates": courses(CourseEvaluator, CourseEvaluator.evaluator_id),
        "instructs": courses(
            CourseInstructor, CourseInstructor.instructor_id, CourseInstructor.deleted_at.is_(None)
        ),
        "mentors": courses(CourseMentor, CourseMentor.mentor_id),
    }
