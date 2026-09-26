from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.services import reference_service, student_extras_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: student extras and reference data"])

Limit = Annotated[int, Query(ge=1, le=MAX_RESULTS)]


class PauseFilters:
    def __init__(
        self,
        enrollment_id: Annotated[int | None, Query()] = None,
        student_id: Annotated[int | None, Query()] = None,
        status: Annotated[
            str | None,
            Query(
                description="paused, resume_requested, resumed or refund_eligible_paused_requested"
            ),
        ] = None,
        request_source: Annotated[str | None, Query(description="student, support or sales")] = None,
        paused_after: Annotated[date | None, Query()] = None,
        paused_before: Annotated[date | None, Query()] = None,
        open_only: Annotated[bool | None, Query(description="Not yet resumed")] = None,
    ):
        self.criteria = {
            "enrollment_id": enrollment_id,
            "student_id": student_id,
            "status": status,
            "request_source": request_source,
            "paused_after": paused_after,
            "paused_before": paused_before,
            "open_only": open_only,
        }


Filters = Annotated[PauseFilters, Depends()]


@router.get("/pause-log/search")
def search_pause_logs(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters, limit: Limit = 10
) -> list[dict]:
    """Pause requests, newest first. The reason text is never returned."""
    return student_extras_service.search_pause_logs(db, limit, **filters.criteria)


@router.get("/pause-log/count")
def count_pause_logs(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    return {"count": student_extras_service.count_pause_logs(db, **filters.criteria)}


@router.get("/pause-log/breakdown")
def breakdown_pause_logs(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[str, Query(description="status, request_source or month")],
    limit: Limit = 10,
) -> list[dict]:
    return student_extras_service.breakdown_pause_logs(db, by, limit, **filters.criteria)


@router.get("/students/{student_id}/availability")
def student_availability(
    student_id: int, current_user: CurrentUser, db: PortalDbSession
) -> list[dict]:
    return student_extras_service.student_availability(db, student_id)


@router.get("/students/{student_id}/notes/count")
def student_note_count(student_id: int, current_user: CurrentUser, db: PortalDbSession) -> dict:
    """How many internal notes exist. The text is not reachable."""
    return student_extras_service.note_count(db, student_id)


@router.get("/heard-about")
def heard_about(
    current_user: CurrentUser,
    db: PortalDbSession,
    student_id: Annotated[int | None, Query(description="One student's answers")] = None,
    limit: Limit = 10,
) -> list[dict]:
    """How students say they found LawSikho: counts, or one student's answers."""
    return student_extras_service.heard_about(db, student_id, limit)


@router.get("/reference/{kind}")
def lookup_reference(
    kind: str,
    response: Response,
    current_user: CurrentUser,
    db: PortalDbSession,
    query: Annotated[str | None, Query(description="Partial name")] = None,
    country_id: Annotated[int | None, Query(description="States only")] = None,
    parent_id: Annotated[int | None, Query(description="Course categories only")] = None,
    limit: Limit = 10,
) -> list[dict]:
    """country, state, tag, course_category or job_role.

    At most `limit` rows; the true number of matches is in `X-Total-Count`.
    """
    response.headers["X-Total-Count"] = str(
        reference_service.count(db, kind, query, country_id, parent_id)
    )
    return reference_service.lookup(db, kind, query, country_id, parent_id, limit)
