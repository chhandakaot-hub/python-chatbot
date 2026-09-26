"""Read-only queries over per-student side tables.

Pause history, availability, "how did you hear about us", and a count-only view
of internal notes. The note body, pause reasons and typed "other" answers are
not mapped -- see `models/portal/student_extras.py`.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.portal.enrollment import Enrollment
from app.models.portal.student_extras import (
    EnrollmentPauseLog,
    HeardAboutAnswer,
    HeardAboutQuestion,
    StudentAvailability,
    StudentNote,
    WeekDay,
)
from app.services.portal_service import MAX_RESULTS, get_student

# Values of enrollment_pause_log_new.status / .request_source, from the enum
# definition in the portal's migration.
PAUSE_STATUSES = {"paused", "resume_requested", "resumed", "refund_eligible_paused_requested"}
PAUSE_SOURCES = {"student", "support", "sales"}

# A student has a handful of availability windows; this only bounds a bad count.
MAX_PER_STUDENT = 50


def _cap(limit: int) -> int:
    return max(1, min(limit, MAX_RESULTS))


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=detail)


# --------------------------------------------------------------------------- #
# Pause history
# --------------------------------------------------------------------------- #
def build_pause_filters(
    enrollment_id: int | None = None,
    student_id: int | None = None,
    status: str | None = None,
    request_source: str | None = None,
    paused_after: date | None = None,
    paused_before: date | None = None,
    open_only: bool | None = None,
) -> list[ColumnElement[bool]]:
    """`student_id` goes through a subquery on enrollments, so no statement
    built from these needs the join."""
    p = EnrollmentPauseLog
    conditions: list[ColumnElement[bool]] = []
    if enrollment_id is not None:
        conditions.append(p.enrollment_id == enrollment_id)
    if student_id is not None:
        conditions.append(
            p.enrollment_id.in_(select(Enrollment.id).where(Enrollment.student_id == student_id))
        )
    if status is not None:
        if status not in PAUSE_STATUSES:
            raise _bad_request(f"status must be one of: {', '.join(sorted(PAUSE_STATUSES))}.")
        conditions.append(p.status == status)
    if request_source is not None:
        if request_source not in PAUSE_SOURCES:
            raise _bad_request(f"request_source must be one of: {', '.join(sorted(PAUSE_SOURCES))}.")
        conditions.append(p.request_source == request_source)
    if paused_after:
        conditions.append(p.paused_at >= paused_after)
    if paused_before:
        conditions.append(p.paused_at < paused_before)
    if open_only is True:
        # Paused and not yet resumed.
        conditions.append(p.resumed_at.is_(None))
    elif open_only is False:
        conditions.append(p.resumed_at.is_not(None))
    return conditions


def search_pause_logs(db: Session, limit: int = 10, **criteria) -> list[dict]:
    p = EnrollmentPauseLog
    statement = (
        select(p.id, p.enrollment_id, p.status, p.request_source, p.paused_at,
               p.resumed_at, p.accepted, p.rejected)
        .where(*build_pause_filters(**criteria))
        .order_by(p.created_at.desc(), p.id.desc())
        .limit(_cap(limit))
    )
    rows = []
    for r in db.execute(statement):
        row = dict(r._mapping)
        row["accepted"] = None if row["accepted"] is None else bool(row["accepted"])
        row["rejected"] = None if row["rejected"] is None else bool(row["rejected"])
        rows.append(row)
    return rows


def count_pause_logs(db: Session, **criteria) -> int:
    return db.scalar(
        select(func.count(EnrollmentPauseLog.id)).where(*build_pause_filters(**criteria))
    ) or 0


def breakdown_pause_logs(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    p = EnrollmentPauseLog
    dimensions: dict[str, ColumnElement] = {
        "status": p.status,
        "request_source": p.request_source,
        "month": func.date_format(p.paused_at, "%Y-%m"),
    }
    if by not in dimensions:
        raise _bad_request(f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.")
    field = dimensions[by]
    statement = (
        select(field.label("value"), func.count(p.id).label("count"))
        .where(*build_pause_filters(**criteria))
        .group_by(field)
        .order_by(field.desc() if by == "month" else func.count(p.id).desc())
        .limit(_cap(limit))
    )
    return [{"value": v, "count": n} for v, n in db.execute(statement)]


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #
def student_availability(db: Session, student_id: int) -> list[dict]:
    """The weekly windows a student said they are free, Monday first."""
    get_student(db, student_id)  # 404 for a student that does not exist
    a = StudentAvailability
    rows = db.execute(
        select(WeekDay.name, a.start_time, a.end_time, a.timezone)
        .select_from(a)
        .join(WeekDay, WeekDay.id == a.weekday_id)
        .where(a.student_id == student_id)
        .order_by(a.weekday_id, a.start_time)
        .limit(MAX_PER_STUDENT)
    )
    return [
        {
            "weekday": day,
            "start_time": start.isoformat(timespec="minutes") if start else None,
            "end_time": end.isoformat(timespec="minutes") if end else None,
            "timezone": tz,
        }
        for day, start, end, tz in rows
    ]


# --------------------------------------------------------------------------- #
# How did you hear about us
# --------------------------------------------------------------------------- #
def heard_about(db: Session, student_id: int | None = None, limit: int = 10) -> list[dict]:
    """Sign-up survey sources: one student's answers, or counts across everyone.

    The survey has been reworded over time, so equivalent sources appear under
    more than one wording; they are reported as stored.
    """
    a = HeardAboutAnswer
    if student_id is not None:
        rows = db.execute(
            select(HeardAboutQuestion.question)
            .select_from(a)
            .join(HeardAboutQuestion, HeardAboutQuestion.id == a.answer_id)
            .where(a.student_id == student_id)
            .limit(MAX_PER_STUDENT)
        )
        return [{"value": q, "count": 1} for (q,) in rows]
    rows = db.execute(
        select(HeardAboutQuestion.question, func.count(a.id))
        .select_from(a)
        .join(HeardAboutQuestion, HeardAboutQuestion.id == a.answer_id)
        .group_by(HeardAboutQuestion.question)
        .order_by(func.count(a.id).desc())
        .limit(_cap(limit))
    )
    return [{"value": q, "count": n} for q, n in rows]


# --------------------------------------------------------------------------- #
# Internal notes -- counts only
# --------------------------------------------------------------------------- #
def note_count(db: Session, student_id: int) -> dict:
    """How many staff notes exist on a student, and when the latest was written.

    The text is deliberately unreachable from here.
    """
    get_student(db, student_id)
    count, latest = db.execute(
        select(func.count(StudentNote.id), func.max(StudentNote.created_at)).where(
            StudentNote.student_id == student_id, StudentNote.deleted_at.is_(None)
        )
    ).one()
    return {"student_id": student_id, "notes": count, "latest_note_at": latest}
