from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.dependencies.auth import CurrentUser, PortalDbSession
from app.services import catalog_service
from app.services.portal_service import MAX_RESULTS

router = APIRouter(tags=["portal: packages, bootcamps, books"])

Limit = Annotated[int, Query(ge=1, le=MAX_RESULTS)]

# The list endpoints return at most MAX_RESULTS rows and say how many matched in
# this header, so the body stays a plain list.
TOTAL_HEADER = "X-Total-Count"


@router.get("/packages")
def find_packages(
    response: Response,
    current_user: CurrentUser,
    db: PortalDbSession,
    name: Annotated[str | None, Query(description="Partial package name")] = None,
    limit: Limit = 10,
) -> list[dict]:
    response.headers[TOTAL_HEADER] = str(catalog_service.count_packages(db, name))
    return catalog_service.find_packages(db, name, limit)


@router.get("/packages/{package_id}")
def get_package(package_id: int, current_user: CurrentUser, db: PortalDbSession) -> dict:
    return catalog_service.get_package(db, package_id)


@router.get("/bootcamps")
def find_bootcamps(
    response: Response,
    current_user: CurrentUser,
    db: PortalDbSession,
    name: Annotated[str | None, Query(description="Partial bootcamp name")] = None,
    refund_course_limit: Annotated[
        int | None,
        Query(
            ge=0,
            description="How many of the bootcamp's courses can be refund eligible "
            "(a count, not a yes/no flag: 1 for most, 2 for a few)",
        ),
    ] = None,
    limit: Limit = 10,
) -> list[dict]:
    response.headers[TOTAL_HEADER] = str(
        catalog_service.count_bootcamps(db, name, refund_course_limit)
    )
    return catalog_service.find_bootcamps(
        db, name, limit, refund_course_limit=refund_course_limit
    )


@router.get("/books")
def find_books(
    response: Response,
    current_user: CurrentUser,
    db: PortalDbSession,
    name: Annotated[str | None, Query()] = None,
    sku: Annotated[str | None, Query()] = None,
    course_id: Annotated[int | None, Query(description="Books this course ships")] = None,
    bootcamp_id: Annotated[int | None, Query(description="Books this bootcamp ships")] = None,
    sendable: Annotated[bool | None, Query()] = None,
    limit: Limit = 10,
) -> list[dict]:
    criteria = dict(
        name=name, sku=sku, course_id=course_id, bootcamp_id=bootcamp_id, sendable=sendable
    )
    response.headers[TOTAL_HEADER] = str(catalog_service.count_books(db, **criteria))
    return catalog_service.find_books(db, limit=limit, **criteria)


class DeliveryFilters:
    """Query parameters shared by search, count and breakdown."""

    def __init__(
        self,
        student_id: Annotated[int | None, Query()] = None,
        enrollment_id: Annotated[int | None, Query()] = None,
        book_id: Annotated[int | None, Query()] = None,
        book_name: Annotated[str | None, Query(description="Partial book name")] = None,
        course_id: Annotated[int | None, Query()] = None,
        bootcamp_id: Annotated[int | None, Query()] = None,
        course_type: Annotated[str | None, Query(description="course or bootcamp")] = None,
        sent: Annotated[bool | None, Query()] = None,
        deliverable: Annotated[bool | None, Query()] = None,
        additional: Annotated[
            bool | None, Query(description="An extra copy, not the standard one")
        ] = None,
        manual: Annotated[
            bool | None, Query(description="Created by hand rather than by the system")
        ] = None,
        country: Annotated[str | None, Query()] = None,
        sent_after: Annotated[date | None, Query()] = None,
        sent_before: Annotated[date | None, Query()] = None,
        created_after: Annotated[date | None, Query()] = None,
        created_before: Annotated[date | None, Query()] = None,
    ):
        self.criteria = {
            "student_id": student_id,
            "enrollment_id": enrollment_id,
            "book_id": book_id,
            "book_name": book_name,
            "course_id": course_id,
            "bootcamp_id": bootcamp_id,
            "course_type": course_type,
            "sent": sent,
            "deliverable": deliverable,
            "additional": additional,
            "manual": manual,
            "country": country,
            "sent_after": sent_after,
            "sent_before": sent_before,
            "created_after": created_after,
            "created_before": created_before,
        }


Filters = Annotated[DeliveryFilters, Depends()]


@router.get("/book-deliveries/search")
def search_deliveries(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters, limit: Limit = 10
) -> list[dict]:
    """Newest deliveries. No name, phone, email or address is ever returned."""
    return catalog_service.search_deliveries(db, limit, **filters.criteria)


@router.get("/book-deliveries/count")
def count_deliveries(
    current_user: CurrentUser, db: PortalDbSession, filters: Filters
) -> dict[str, int]:
    return {"count": catalog_service.count_deliveries(db, **filters.criteria)}


@router.get("/book-deliveries/breakdown")
def breakdown_deliveries(
    current_user: CurrentUser,
    db: PortalDbSession,
    filters: Filters,
    by: Annotated[
        str,
        Query(
            description="book, sent, deliverable, course_type, additional, country, state, "
            "course, created_month or sent_month"
        ),
    ],
    limit: Limit = 10,
) -> list[dict]:
    return catalog_service.breakdown_deliveries(db, by, limit, **filters.criteria)
