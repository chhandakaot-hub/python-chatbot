from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.schemas.enrollment import (
    EnrollmentOut,
    EnrollmentSummary,
    enrollment_out,
    enrollment_summary,
)
from app.services import enrollment_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: enrollments"])


class EnrollmentFilters:
    """Query parameters shared by search, count and breakdown.

    A class dependency, so the three endpoints cannot accept different
    criteria -- the same guarantee `build_filters()` gives the service.
    """

    def __init__(
        self,
        student_id: Annotated[int | None, Query()] = None,
        course_id: Annotated[int | None, Query()] = None,
        course_name: Annotated[str | None, Query(description="Partial course name")] = None,
        batch_id: Annotated[int | None, Query()] = None,
        status: Annotated[
            int | None,
            Query(
                ge=0,
                le=4,
                description="0 pending, 1 active, 2 paused, 3 resume requested, "
                "4 pause requested (refund eligible)",
            ),
        ] = None,
        enrollment_type: Annotated[
            int | None,
            Query(ge=1, le=4, description="1 normal, 2 package, 3 bootcamp, 4 package batch"),
        ] = None,
        completed: Annotated[bool | None, Query()] = None,
        is_certified: Annotated[bool | None, Query()] = None,
        paused: Annotated[bool | None, Query(description="Currently paused (status 2)")] = None,
        enrolled_after: Annotated[date | None, Query()] = None,
        enrolled_before: Annotated[date | None, Query()] = None,
        expiring_before: Annotated[date | None, Query(description="Course expiry before")] = None,
        min_progress: Annotated[float | None, Query(ge=0, le=100)] = None,
        max_progress: Annotated[float | None, Query(ge=0, le=100)] = None,
    ):
        self.criteria = {
            "student_id": student_id,
            "course_id": course_id,
            "course_name": course_name,
            "batch_id": batch_id,
            "status": status,
            "enrollment_type": enrollment_type,
            "completed": completed,
            "is_certified": is_certified,
            "paused": paused,
            "enrolled_after": enrolled_after,
            "enrolled_before": enrolled_before,
            "expiring_before": expiring_before,
            "min_progress": min_progress,
            "max_progress": max_progress,
        }


Filters = Annotated[EnrollmentFilters, Depends()]


@router.get("/enrollments/search", response_model=list[EnrollmentSummary])
def search_enrollments(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
):
    """Newest enrollments matching every criterion given."""
    rows = enrollment_service.list_enrollments(db, limit=limit, **filters.criteria)
    return [enrollment_summary(e) for e in rows]


@router.get("/enrollments/count")
def count_enrollments(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    """True number of matches, not capped by the page size."""
    return {"count": enrollment_service.count_enrollments(db, **filters.criteria)}


@router.get("/enrollments/breakdown")
def breakdown_enrollments(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[
        str, Query(description="course, status, type, completed, is_certified, batch or month")
    ],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return enrollment_service.breakdown_enrollments(db, by, limit=limit, **filters.criteria)


@router.get("/enrollments/{enrollment_id}", response_model=EnrollmentOut)
def get_enrollment(enrollment_id: int, current_user: CurrentUser, db: PortalDbSession):
    return enrollment_out(enrollment_service.get_enrollment(db, enrollment_id))


@router.get("/students/{student_id}/enrollments", response_model=list[EnrollmentSummary])
def student_enrollments(student_id: int, current_user: CurrentUser, db: PortalDbSession):
    """Every enrollment for one student, newest first."""
    return [enrollment_summary(e) for e in enrollment_service.student_enrollments(db, student_id)]


@router.get("/courses/search")
def search_courses(
    current_user: CurrentUser,
    db: PortalDbSession,
    q: Annotated[str, Query(min_length=2, description="Partial course name")],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return [
        {
            "id": c.id,
            "course_name": c.course_name,
            "status": c.status,
            "duration_days": c.duration_days,
        }
        for c in enrollment_service.find_courses(db, q, limit)
    ]
