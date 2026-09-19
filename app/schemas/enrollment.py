"""Shapes for portal enrollments.

Built with `enrollment_out()` / `enrollment_summary()` rather than plain
`from_attributes`, because course name and batch label live on related rows,
because the portal's 0/1 integer flags read better as real booleans, and
because a stored code like status 4 means nothing to a person -- or to the
model -- without its label.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.portal.codes import ENROLLMENT_STATUS_LABELS, ENROLLMENT_TYPE_LABELS, label
from app.models.portal.enrollment import Enrollment


class EnrollmentSummary(BaseModel):
    """One line per enrollment: used for lists and for AI tool results."""

    id: int
    student_id: int
    course_id: int
    course_name: str | None
    batch_label: str | None
    status: int
    status_label: str
    type_label: str
    current_percent: float
    completed: bool
    is_certified: bool
    enrolled_at: datetime


class EnrollmentOut(EnrollmentSummary):
    """Full detail for a single enrollment."""

    enrollment_code: str | None
    batch_id: int | None
    type: str | None
    course_activated: bool | None
    bootcamp_name: str | None

    pause_status: str | None
    paused_at: datetime | None

    completed_at: datetime | None
    mcq_completed: bool | None
    mcq_score: float | None
    certified_at: datetime | None

    enrollment_expire_at: datetime | None
    course_expiry_date: datetime | None


def _flag(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def enrollment_summary(e: Enrollment) -> EnrollmentSummary:
    """Requires `course` and `batch` to be eager-loaded (they are lazy='raise')."""
    return EnrollmentSummary(
        id=e.id,
        student_id=e.student_id,
        course_id=e.course_id,
        course_name=e.course.course_name if e.course else None,
        batch_label=e.batch.batch_date if e.batch else None,
        status=e.status,
        status_label=label(ENROLLMENT_STATUS_LABELS, e.status),
        type_label=label(ENROLLMENT_TYPE_LABELS, e.type),
        current_percent=float(e.current_percent or 0),
        completed=bool(e.completed),
        is_certified=bool(e.is_certified),
        enrolled_at=e.created_at,
    )


def enrollment_out(e: Enrollment) -> EnrollmentOut:
    """Requires `course` and `batch` to be eager-loaded (they are lazy='raise')."""
    return EnrollmentOut(
        **enrollment_summary(e).model_dump(),
        enrollment_code=e.enrollment_code,
        batch_id=e.batch_id,
        type=e.type,
        course_activated=_flag(e.course_activated),
        bootcamp_name=e.bootcamp_name,
        pause_status=e.pause_status,
        paused_at=e.paused_at,
        completed_at=e.completed_at,
        mcq_completed=_flag(e.mcq_completed),
        mcq_score=float(e.mcq_score) if e.mcq_score is not None else None,
        certified_at=e.certified_datetime,
        enrollment_expire_at=e.enrollment_expire_at,
        course_expiry_date=e.course_expiry_date,
    )
