from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.services import staff_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: staff and roles"])


class StaffFilters:
    def __init__(
        self,
        query: Annotated[str | None, Query(description="Partial name")] = None,
        role: Annotated[str | None, Query(description="Partial role name, e.g. Evaluator")] = None,
        status: Annotated[
            int | None,
            Query(ge=0, le=3, description="0 disabled, 1 approved, 2 blocked, 3 pending"),
        ] = None,
        never_logged_in: Annotated[bool | None, Query()] = None,
    ):
        self.criteria = {
            "query": query,
            "role": role,
            "status": status,
            "never_logged_in": never_logged_in,
        }


Filters = Annotated[StaffFilters, Depends()]


@router.get("/staff/search")
def search_staff(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    """Staff by name, role or status. Email and phone are never returned."""
    return staff_service.find_staff(db, limit, **filters.criteria)


@router.get("/staff/count")
def count_staff(current_user: CurrentUser, db: PortalDbSession, filters: Filters) -> dict[str, int]:
    return {"count": staff_service.count_staff(db, **filters.criteria)}


@router.get("/staff/breakdown")
def breakdown_staff(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[str, Query(description="role or status")],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return staff_service.breakdown_staff(db, by, limit, **filters.criteria)


@router.get("/staff/{staff_id}/courses")
def staff_courses(staff_id: int, current_user: CurrentUser, db: PortalDbSession) -> dict:
    return staff_service.staff_courses(db, staff_id)


@router.get("/courses/{course_id}/staff")
def course_staff(course_id: int, current_user: CurrentUser, db: PortalDbSession) -> dict:
    """Evaluators, instructors and mentors attached to one course."""
    return staff_service.course_staff(db, course_id)
