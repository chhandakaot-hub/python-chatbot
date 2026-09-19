"""Shapes for course completion.

`state` is the verdict, `completed` the raw column behind it. Both are
reported, because they disagree for thousands of enrollments and a reader who
sees only one of them cannot tell which question was answered. See
`app/services/completion_service.py` for the rule.
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.portal.enrollment import Enrollment
from app.services.completion_service import completion_state


class CompletionSummary(BaseModel):
    """One line per enrollment: used for lists and for AI tool results."""

    enrollment_id: int
    student_id: int
    course_id: int
    course_name: str | None
    batch_label: str | None
    state: str
    completed: bool
    mcq_required: bool
    mcq_completed: bool
    is_certified: bool
    current_percent: float
    completed_at: datetime | None


class CompletionOut(CompletionSummary):
    """Full detail for one enrollment's completion."""

    enrollment_code: str | None
    subjective_percent: float | None
    written_percent: float | None
    mcq_score: float | None
    certified_at: datetime | None
    course_expiry_date: datetime | None
    enrolled_at: datetime | None


class CompletionSummaryCounts(BaseModel):
    """The three states plus certificates, over a filtered set of enrollments."""

    total: int
    completed: int
    awaiting_mcq: int
    not_completed: int
    certified: int


class SubmissionProgress(BaseModel):
    """Coursework handed in, alongside the verdict."""

    assigned: int
    submitted: int
    outstanding: int
    mandatory: int


def _percent(value) -> float | None:
    return None if value is None else float(value)


def completion_summary(e: Enrollment, mcq_required: bool) -> CompletionSummary:
    """Requires `course` and `batch` to be eager-loaded (they are lazy='raise').

    `mcq_required` is derived in SQL, not a column -- the service returns it
    alongside each row.
    """
    return CompletionSummary(
        enrollment_id=e.id,
        student_id=e.student_id,
        course_id=e.course_id,
        course_name=e.course.course_name if e.course else None,
        batch_label=e.batch.batch_date if e.batch else None,
        state=completion_state(e.completed, e.mcq_completed, mcq_required),
        completed=bool(e.completed),
        mcq_required=bool(mcq_required),
        mcq_completed=bool(e.mcq_completed),
        is_certified=bool(e.is_certified),
        current_percent=float(e.current_percent or 0),
        completed_at=e.completed_at,
    )


def completion_out(e: Enrollment, mcq_required: bool) -> CompletionOut:
    """Same eager-loading requirements as `completion_summary`."""
    return CompletionOut(
        **completion_summary(e, mcq_required).model_dump(),
        enrollment_code=e.enrollment_code,
        subjective_percent=_percent(e.subjective_passing_percent),
        written_percent=_percent(e.written_passing_percent),
        mcq_score=_percent(e.mcq_score),
        certified_at=e.certified_datetime,
        course_expiry_date=e.course_expiry_date,
        enrolled_at=e.created_at,
    )
