from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.schemas.completion import (
    CompletionOut,
    CompletionSummary,
    CompletionSummaryCounts,
    SubmissionProgress,
    completion_out,
    completion_summary,
)
from app.services import completion_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: course completion"])


class CompletionFilters:
    """Query parameters shared by search, count, summary and breakdown."""

    def __init__(
        self,
        student_id: Annotated[int | None, Query()] = None,
        course_id: Annotated[int | None, Query()] = None,
        course_name: Annotated[str | None, Query(description="Partial course name")] = None,
        batch_id: Annotated[int | None, Query()] = None,
        state: Annotated[
            str | None,
            Query(
                description="The completion verdict: completed, awaiting_mcq or "
                "not_completed. Not the same as the raw `completed` column -- an "
                "enrollment with completed=1 on an MCQ course is awaiting_mcq until "
                "the MCQ is confirmed."
            ),
        ] = None,
        mcq_required: Annotated[
            bool | None, Query(description="Course requires an LMS MCQ to finish")
        ] = None,
        is_certified: Annotated[bool | None, Query()] = None,
        min_percent: Annotated[float | None, Query(ge=0, le=100)] = None,
        max_percent: Annotated[float | None, Query(ge=0, le=100)] = None,
        completed_after: Annotated[date | None, Query()] = None,
        completed_before: Annotated[date | None, Query()] = None,
        certified_after: Annotated[date | None, Query()] = None,
        expiring_before: Annotated[date | None, Query(description="Course expiry before")] = None,
    ):
        self.criteria = {
            "student_id": student_id,
            "course_id": course_id,
            "course_name": course_name,
            "batch_id": batch_id,
            "state": state,
            "mcq_required": mcq_required,
            "is_certified": is_certified,
            "min_percent": min_percent,
            "max_percent": max_percent,
            "completed_after": completed_after,
            "completed_before": completed_before,
            "certified_after": certified_after,
            "expiring_before": expiring_before,
        }


Filters = Annotated[CompletionFilters, Depends()]


@router.get("/completion/search", response_model=list[CompletionSummary])
def search_completions(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
):
    """Completion state per enrollment, most recently completed first."""
    rows = completion_service.list_completions(db, limit=limit, **filters.criteria)
    return [completion_summary(e, mcq) for e, mcq in rows]


@router.get("/completion/count")
def count_completions(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    """True number of matches, not capped by the page size."""
    return {"count": completion_service.count_completions(db, **filters.criteria)}


@router.get("/completion/summary", response_model=CompletionSummaryCounts)
def completion_summary_counts(current_user: CurrentUser, db: PortalDbSession, filters: Filters):
    """The three completion states plus certificates, in one pass.

    The three states add up to `total`, so a reader can see at a glance how
    many "completed" enrollments are really still waiting on an MCQ.
    """
    return completion_service.summarise(db, **filters.criteria)


@router.get("/completion/breakdown")
def breakdown_completions(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[
        str, Query(description="state, course, mcq_required, is_certified or completed_month")
    ],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return completion_service.breakdown_completions(db, by, limit=limit, **filters.criteria)


@router.get("/completion/{enrollment_id}", response_model=CompletionOut)
def get_completion(enrollment_id: int, current_user: CurrentUser, db: PortalDbSession):
    """One enrollment's completion state in full."""
    enrollment, mcq_required = completion_service.get_completion(db, enrollment_id)
    return completion_out(enrollment, mcq_required)


@router.get("/completion/{enrollment_id}/progress", response_model=SubmissionProgress)
def completion_progress(enrollment_id: int, current_user: CurrentUser, db: PortalDbSession):
    """Coursework handed in for one enrollment.

    "Not completed" alone does not say whether a student is one assignment
    short or has not started; this is the number behind that.
    """
    return completion_service.submission_progress(db, enrollment_id)


@router.get("/students/{student_id}/completion", response_model=list[CompletionSummary])
def student_completion(student_id: int, current_user: CurrentUser, db: PortalDbSession):
    """Completion state for every course one student is enrolled in."""
    rows = completion_service.student_completions(db, student_id)
    return [completion_summary(e, mcq) for e, mcq in rows]
