from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.schemas.assignment import (
    AssignmentOut,
    AssignmentSummary,
    StudentAssignmentOut,
    StudentAssignmentSummary,
    assignment_out,
    assignment_summary,
    student_assignment_out,
    student_assignment_summary,
)
from app.services import assignment_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: assignments"])


class AssignmentFilters:
    """Query parameters shared by search, count and breakdown.

    A class dependency, so the three endpoints cannot accept different
    criteria -- the same guarantee `build_assignment_filters()` gives.
    """

    def __init__(
        self,
        course_id: Annotated[int | None, Query()] = None,
        course_name: Annotated[str | None, Query(description="Partial course name")] = None,
        topic: Annotated[str | None, Query(description="Partial topic title")] = None,
        assignment_code: Annotated[str | None, Query(description="Partial code")] = None,
        assignment_type: Annotated[
            int | None, Query(ge=0, le=1, description="0 subjective, 1 written")
        ] = None,
        status: Annotated[
            int | None, Query(ge=0, le=1, description="0 deactivated, 1 active")
        ] = None,
        plagiarism_checked: Annotated[bool | None, Query()] = None,
        min_exercises: Annotated[int | None, Query(ge=0)] = None,
        max_exercises: Annotated[int | None, Query(ge=0)] = None,
        created_after: Annotated[date | None, Query()] = None,
        created_before: Annotated[date | None, Query()] = None,
    ):
        self.criteria = {
            "course_id": course_id,
            "course_name": course_name,
            "topic": topic,
            "assignment_code": assignment_code,
            "assignment_type": assignment_type,
            "status": status,
            "plagiarism_checked": plagiarism_checked,
            "min_exercises": min_exercises,
            "max_exercises": max_exercises,
            "created_after": created_after,
            "created_before": created_before,
        }


class StudentAssignmentFilters:
    """Query parameters shared by the student-assignment endpoints."""

    def __init__(
        self,
        enrollment_id: Annotated[int | None, Query()] = None,
        student_id: Annotated[int | None, Query()] = None,
        assignment_id: Annotated[int | None, Query()] = None,
        course_id: Annotated[int | None, Query()] = None,
        course_name: Annotated[str | None, Query(description="Partial course name")] = None,
        status: Annotated[
            int | None,
            Query(
                ge=0,
                le=5,
                description="0 deactivated, 1 active (not submitted), 2 pending, "
                "3 submitted, 4 resubmitted, 5 evaluated",
            ),
        ] = None,
        submitted: Annotated[
            bool | None, Query(description="Handed in: status 3, 4 or 5")
        ] = None,
        mandatory: Annotated[bool | None, Query()] = None,
        overdue: Annotated[
            bool | None, Query(description="Deadline passed with nothing handed in")
        ] = None,
        due_after: Annotated[date | None, Query()] = None,
        due_before: Annotated[date | None, Query()] = None,
        min_submits: Annotated[int | None, Query(ge=0)] = None,
    ):
        self.criteria = {
            "enrollment_id": enrollment_id,
            "student_id": student_id,
            "assignment_id": assignment_id,
            "course_id": course_id,
            "course_name": course_name,
            "status": status,
            "submitted": submitted,
            "mandatory": mandatory,
            "overdue": overdue,
            "due_after": due_after,
            "due_before": due_before,
            "min_submits": min_submits,
        }


Filters = Annotated[AssignmentFilters, Depends()]
StudentFilters = Annotated[StudentAssignmentFilters, Depends()]


# --------------------------------------------------------------------------- #
# assignments -- what a course sets
# --------------------------------------------------------------------------- #
@router.get("/assignments/search", response_model=list[AssignmentSummary])
def search_assignments(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
):
    """Assignments matching every criterion given."""
    rows = assignment_service.list_assignments(db, limit=limit, **filters.criteria)
    return [assignment_summary(a) for a in rows]


@router.get("/assignments/count")
def count_assignments(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    """True number of matches, not capped by the page size."""
    return {"count": assignment_service.count_assignments(db, **filters.criteria)}


@router.get("/assignments/breakdown")
def breakdown_assignments(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[str, Query(description="course, topic, type, status, plagiarism or month")],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return assignment_service.breakdown_assignments(db, by, limit=limit, **filters.criteria)


@router.get("/assignments/{assignment_id}", response_model=AssignmentOut)
def get_assignment(assignment_id: int, current_user: CurrentUser, db: PortalDbSession):
    return assignment_out(assignment_service.get_assignment(db, assignment_id))


# --------------------------------------------------------------------------- #
# student assignments -- what one student was given
# --------------------------------------------------------------------------- #
@router.get("/student-assignments/search", response_model=list[StudentAssignmentSummary])
def search_student_assignments(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: StudentFilters,
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
):
    """Student assignments matching every criterion given."""
    rows = assignment_service.list_student_assignments(db, limit=limit, **filters.criteria)
    return [student_assignment_summary(sa) for sa in rows]


@router.get("/student-assignments/count")
def count_student_assignments(
    current_user: CurrentUser, db: PortalDbSession, filters: StudentFilters
) -> dict[str, int]:
    """True number of matches, not capped by the page size."""
    return {"count": assignment_service.count_student_assignments(db, **filters.criteria)}


@router.get("/student-assignments/breakdown")
def breakdown_student_assignments(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: StudentFilters,
    by: Annotated[
        str, Query(description="status, course, mandatory, submit_counter, topic or month")
    ],
    limit: Annotated[int, Query(ge=1, le=MAX_RESULTS)] = 10,
) -> list[dict]:
    return assignment_service.breakdown_student_assignments(
        db, by, limit=limit, **filters.criteria
    )


@router.get("/student-assignments/{student_assignment_id}", response_model=StudentAssignmentOut)
def get_student_assignment(
    student_assignment_id: int, current_user: CurrentUser, db: PortalDbSession
):
    return student_assignment_out(
        assignment_service.get_student_assignment(db, student_assignment_id)
    )


@router.get("/enrollments/{enrollment_id}/assignments", response_model=list[StudentAssignmentSummary])
def enrollment_assignments(enrollment_id: int, current_user: CurrentUser, db: PortalDbSession):
    """Every assignment for one enrollment, in the order the student sees them."""
    rows = assignment_service.enrollment_assignments(db, enrollment_id)
    return [student_assignment_summary(sa) for sa in rows]
