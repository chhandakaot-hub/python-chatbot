from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.services import feedback_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: feedback (CSAT / NPS)"])

# `kind` is one of: assignment_csat, class_csat, evaluator_csat, nps, nps_v2.
# It is validated by the service, which answers 400 with the allowed list.


class FeedbackFilters:
    """Query parameters shared by search, count, summary and breakdown.

    A filter the chosen kind does not have is answered with 400 by the service,
    never ignored.
    """

    def __init__(
        self,
        student_id: Annotated[int | None, Query()] = None,
        course_id: Annotated[int | None, Query()] = None,
        batch_id: Annotated[int | None, Query()] = None,
        enrollment_id: Annotated[int | None, Query()] = None,
        assignment_id: Annotated[int | None, Query()] = None,
        result_id: Annotated[int | None, Query()] = None,
        evaluator_id: Annotated[int | None, Query()] = None,
        class_date_relation_id: Annotated[int | None, Query()] = None,
        survey_type: Annotated[
            str | None, Query(description="nps: one_month or completed; nps_v2: a day count")
        ] = None,
        min_rating: Annotated[int | None, Query(ge=0, le=10)] = None,
        max_rating: Annotated[int | None, Query(ge=0, le=10)] = None,
        created_after: Annotated[date | None, Query()] = None,
        created_before: Annotated[date | None, Query()] = None,
    ):
        self.criteria = {
            "student_id": student_id,
            "course_id": course_id,
            "batch_id": batch_id,
            "enrollment_id": enrollment_id,
            "assignment_id": assignment_id,
            "result_id": result_id,
            "evaluator_id": evaluator_id,
            "class_date_relation_id": class_date_relation_id,
            "survey_type": survey_type,
            "min_rating": min_rating,
            "max_rating": max_rating,
            "created_after": created_after,
            "created_before": created_before,
        }


Filters = Annotated[FeedbackFilters, Depends()]


@router.get("/feedback/nps-scores")
def nps_scores(
    current_user: CurrentUser,
    db: PortalDbSession,
    scope: Annotated[str, Query(description="course or bootcamp")] = "course",
    name: Annotated[str | None, Query(description="Partial name")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    """The portal's precomputed NPS per course or bootcamp, best first."""
    return feedback_service.nps_scores(db, scope, name, limit)


@router.get("/feedback/{kind}/search")
def search_feedback(
    kind: str,
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    """Newest responses, each with the reason options the student ticked."""
    return feedback_service.search_feedback(db, kind, limit, **filters.criteria)


@router.get("/feedback/{kind}/count")
def count_feedback(
    kind: str, current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    return {"count": feedback_service.count_feedback(db, kind, **filters.criteria)}


@router.get("/feedback/{kind}/summary")
def feedback_summary(
    kind: str, current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict:
    """Responses, average rating, rating spread -- and the NPS for nps kinds."""
    return feedback_service.feedback_summary(db, kind, **filters.criteria)


@router.get("/feedback/{kind}/breakdown")
def breakdown_feedback(
    kind: str,
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[str, Query(description="rating, month, course, reason, evaluator, ...")],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
    sort: Annotated[str, Query(description="count (default), average_rating or lowest_rating")] = "count",
    min_responses: Annotated[
        int | None, Query(ge=1, description="Drop groups with fewer responses")
    ] = None,
) -> list[dict]:
    return feedback_service.breakdown_feedback(
        db, kind, by, limit, sort, min_responses, **filters.criteria
    )
