from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.schemas.student import StudentOut, StudentSummary
from app.services import portal_service

router = APIRouter(prefix="/students", tags=["portal: students"])

# Shared by search and count so the two always accept the same criteria.
Q = Annotated[str | None, Query(description="Name, email, or registration code")]
City = Annotated[str | None, Query()]
State = Annotated[str | None, Query()]
Country = Annotated[str | None, Query()]
Status = Annotated[int | None, Query(ge=0, le=2, description="0 = pending, 1 = active, 2 = disabled")]
Activity = Annotated[str | None, Query(description="Matches 'what are you doing currently'")]
After = Annotated[date | None, Query(description="Registered on or after this date")]
Before = Annotated[date | None, Query(description="Registered before this date")]
LoginAfter = Annotated[date | None, Query(description="Last logged in on or after")]
NeverLoggedIn = Annotated[bool | None, Query(description="True = has never logged in")]


@router.get("/search", response_model=list[StudentSummary])
def search_students(
    current_user: CurrentUser,
    db: PortalDbSession,
    q: Q = None,
    city: City = None,
    state: State = None,
    country: Country = None,
    status: Status = None,
    current_activity: Activity = None,
    registered_after: After = None,
    registered_before: Before = None,
    last_login_after: LoginAfter = None,
    never_logged_in: NeverLoggedIn = None,
    limit: Annotated[int, Query(ge=1, le=portal_service.MAX_RESULTS)] = 10,
):
    """Newest students matching every criterion given. All criteria optional."""
    return portal_service.search_students(
        db,
        limit=limit,
        query=q,
        city=city,
        state=state,
        country=country,
        status=status,
        current_activity=current_activity,
        registered_after=registered_after,
        registered_before=registered_before,
        last_login_after=last_login_after,
        never_logged_in=never_logged_in,
    )


@router.get("/count")
def count_students(
    current_user: CurrentUser,
    db: PortalDbSession,
    q: Q = None,
    city: City = None,
    state: State = None,
    country: Country = None,
    status: Status = None,
    current_activity: Activity = None,
    registered_after: After = None,
    registered_before: Before = None,
    last_login_after: LoginAfter = None,
    never_logged_in: NeverLoggedIn = None,
) -> dict[str, int]:
    """True number of matches -- not capped by the search page size."""
    return {
        "count": portal_service.count_students(
            db,
            query=q,
            city=city,
            state=state,
            country=country,
            status=status,
            current_activity=current_activity,
            registered_after=registered_after,
            registered_before=registered_before,
            last_login_after=last_login_after,
            never_logged_in=never_logged_in,
        )
    }


@router.get("/breakdown")
def breakdown(
    current_user: CurrentUser,
    db: PortalDbSession,
    by: Annotated[str, Query(description="country, state, city, status or current_activity")],
    limit: Annotated[int, Query(ge=1, le=portal_service.MAX_RESULTS)] = 10,
    country: Country = None,
    status: Status = None,
) -> list[dict]:
    """Counts per value of one column, e.g. students per state within India."""
    return portal_service.group_students(db, by, limit=limit, country=country, status=status)


@router.get("/{student_id}", response_model=StudentOut)
def get_student(student_id: int, current_user: CurrentUser, db: PortalDbSession):
    return portal_service.get_student(db, student_id)
