from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.schemas.result import ResultOut, ResultSummary, result_out, result_summary
from app.services import result_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: results"])


class ResultFilters:
    """Query parameters shared by search, count and breakdown.

    A class dependency, so the three endpoints cannot accept different
    criteria -- the same guarantee `build_filters()` gives the service.
    """

    def __init__(
        self,
        student_id: Annotated[int | None, Query()] = None,
        student_assignment_id: Annotated[
            int | None,
            Query(description="A student_assignments id (the portal stores it as assignment_id)"),
        ] = None,
        enrollment_id: Annotated[int | None, Query()] = None,
        course_id: Annotated[int | None, Query()] = None,
        course_name: Annotated[str | None, Query(description="Partial course name")] = None,
        status: Annotated[
            int | None,
            Query(
                ge=0,
                le=5,
                description="0 deactivated, 1 active (awaiting evaluation), 2 pending, "
                "3 resubmission requested, 5 evaluated (there is no 4)",
            ),
        ] = None,
        evaluated: Annotated[bool | None, Query()] = None,
        latest_only: Annotated[
            bool | None, Query(description="Only the current submission of each assignment")
        ] = None,
        awaiting_evaluation: Annotated[bool | None, Query()] = None,
        overdue_evaluation: Annotated[
            bool | None, Query(description="Past the evaluation due date, still unevaluated")
        ] = None,
        plagiarism_flagged: Annotated[bool | None, Query()] = None,
        ai_evaluated: Annotated[bool | None, Query()] = None,
        min_score: Annotated[float | None, Query(ge=0)] = None,
        max_score: Annotated[float | None, Query(ge=0)] = None,
        submitted_after: Annotated[date | None, Query()] = None,
        submitted_before: Annotated[date | None, Query()] = None,
        evaluated_after: Annotated[date | None, Query()] = None,
    ):
        self.criteria = {
            "student_id": student_id,
            "student_assignment_id": student_assignment_id,
            "enrollment_id": enrollment_id,
            "course_id": course_id,
            "course_name": course_name,
            "status": status,
            "evaluated": evaluated,
            "latest_only": latest_only,
            "awaiting_evaluation": awaiting_evaluation,
            "overdue_evaluation": overdue_evaluation,
            "plagiarism_flagged": plagiarism_flagged,
            "ai_evaluated": ai_evaluated,
            "min_score": min_score,
            "max_score": max_score,
            "submitted_after": submitted_after,
            "submitted_before": submitted_before,
            "evaluated_after": evaluated_after,
        }


Filters = Annotated[ResultFilters, Depends()]


@router.get("/results/search", response_model=list[ResultSummary])
def search_results(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
):
    """Most recent submissions matching every criterion given."""
    rows = result_service.list_results(db, limit=limit, **filters.criteria)
    return [result_summary(r) for r in rows]


@router.get("/results/count")
def count_results(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    """True number of matches, not capped by the page size.

    Counts submissions. Pass `latest_only=true` to count assignments instead:
    a student who resubmitted three times has three rows here.
    """
    return {"count": result_service.count_results(db, **filters.criteria)}


@router.get("/results/breakdown")
def breakdown_results(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[
        str,
        Query(
            description="status, course, plagiarism, review_done, ai_evaluation_status, "
            "assignment_type, submitted_month or evaluated_month"
        ),
    ],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return result_service.breakdown_results(db, by, limit=limit, **filters.criteria)


@router.get("/results/{result_id}", response_model=ResultOut)
def get_result(result_id: int, current_user: CurrentUser, db: PortalDbSession):
    return result_out(result_service.get_result(db, result_id))


@router.get("/students/{student_id}/results", response_model=list[ResultSummary])
def student_results(student_id: int, current_user: CurrentUser, db: PortalDbSession):
    """One student's current submissions, newest first (superseded ones omitted)."""
    return [result_summary(r) for r in result_service.student_results(db, student_id)]
