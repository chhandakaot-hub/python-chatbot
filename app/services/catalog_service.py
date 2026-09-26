"""Read-only queries over packages, bootcamps, books and book deliveries.

Same rules as the other portal services. Book deliveries are the big table
(~38k rows); everything about them goes through one filter builder so a list,
its count and its breakdown cannot disagree. Personal columns of the delivery
log are not mapped at all -- see `models/portal/catalog.py`.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from app.models.portal.catalog import (
    Book,
    BookDelivery,
    Bootcamp,
    BootcampBook,
    CourseBook,
    Package,
    PackageCourse,
)
from app.models.portal.course import Course
from app.services.portal_service import MAX_RESULTS

# `book_delivery_log.course_type` and `is_additional` hold these literal
# strings (BookDeliveryLog.php).
BOOTCAMP_COURSE = "BOOTCAMP"
NORMAL_COURSE = "NORMAL COURSE"
IS_ADDITIONAL = "IS ADDITIONAL"


# Breakdowns over 0/1 columns get a word beside the number.
_BOOLEAN_LABELS = {
    "sent": {True: "sent", False: "not sent"},
    "deliverable": {True: "deliverable", False: "not deliverable"},
}


def _cap(limit: int) -> int:
    return max(1, min(limit, MAX_RESULTS))


# --------------------------------------------------------------------------- #
# Packages and bootcamps
# --------------------------------------------------------------------------- #
def _package_courses(db: Session, package_ids: list[int]) -> dict[int, list[dict]]:
    if not package_ids:
        return {}
    rows = db.execute(
        select(PackageCourse.package_id, Course.id, Course.course_name)
        .select_from(PackageCourse)
        .join(Course, Course.id == PackageCourse.course_id)
        .where(PackageCourse.package_id.in_(package_ids), Course.deleted_at.is_(None))
        .order_by(Course.course_name)
    )
    out: dict[int, list[dict]] = {}
    for package_id, course_id, name in rows:
        out.setdefault(package_id, []).append({"course_id": course_id, "course_name": name})
    return out


def _package_query(*columns, name: str | None = None) -> Select:
    """Shared by the list and its count, so the two cannot disagree."""
    statement = select(*columns).select_from(Package).where(Package.deleted_at.is_(None))
    if name and name.strip():
        statement = statement.where(Package.name.like(f"%{name.strip()}%"))
    return statement


def count_packages(db: Session, name: str | None = None) -> int:
    """True number of matching packages -- not the capped list length."""
    return db.scalar(_package_query(func.count(Package.id), name=name)) or 0


def find_packages(db: Session, name: str | None = None, limit: int = 10) -> list[dict]:
    """Packages by partial name, each with the courses it includes."""
    statement = _package_query(Package, name=name)
    packages = list(db.scalars(statement.order_by(Package.name).limit(_cap(limit))))
    courses = _package_courses(db, [p.id for p in packages])
    return [
        {
            "id": p.id,
            "name": p.name,
            "duration_days": p.duration_days,
            "courses": courses.get(p.id, []),
        }
        for p in packages
    ]


def get_package(db: Session, package_id: int) -> dict:
    package = db.scalar(
        select(Package).where(Package.id == package_id, Package.deleted_at.is_(None))
    )
    if package is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail="Package not found"
        )
    return {
        "id": package.id,
        "name": package.name,
        "duration_days": package.duration_days,
        "courses": _package_courses(db, [package.id]).get(package.id, []),
    }


def _bootcamp_query(
    *columns, name: str | None = None, refund_course_limit: int | None = None
) -> Select:
    """Shared by the list and its count, so the two cannot disagree."""
    statement = select(*columns).select_from(Bootcamp)
    if name and name.strip():
        statement = statement.where(Bootcamp.name.like(f"%{name.strip()}%"))
    if refund_course_limit is not None:
        statement = statement.where(Bootcamp.refund_eligible_course == refund_course_limit)
    return statement


def count_bootcamps(
    db: Session, name: str | None = None, refund_course_limit: int | None = None
) -> int:
    """True number of matching bootcamps -- not the capped list length."""
    return db.scalar(
        _bootcamp_query(func.count(Bootcamp.id), name=name, refund_course_limit=refund_course_limit)
    ) or 0


def find_bootcamps(
    db: Session,
    name: str | None = None,
    limit: int = 10,
    refund_course_limit: int | None = None,
) -> list[dict]:
    """Bootcamps by partial name and/or refund limit, each with its books.

    `refund_course_limit` is how many of a bootcamp's courses can be refund
    eligible (see `models/portal/catalog.py`); it is not a yes/no flag.
    """
    statement = _bootcamp_query(Bootcamp, name=name, refund_course_limit=refund_course_limit)
    bootcamps = list(db.scalars(statement.order_by(Bootcamp.name).limit(_cap(limit))))

    books: dict[int, list[dict]] = {}
    if bootcamps:
        rows = db.execute(
            select(BootcampBook.bootcamp_id, Book.id, Book.name, BootcampBook.delivery_start_date)
            .select_from(BootcampBook)
            .join(Book, Book.id == BootcampBook.book_id)
            .where(BootcampBook.bootcamp_id.in_([b.id for b in bootcamps]))
            .order_by(Book.name)
        )
        for bootcamp_id, book_id, book_name, start in rows:
            books.setdefault(bootcamp_id, []).append(
                {"book_id": book_id, "book_name": book_name, "delivery_start_date": start}
            )
    return [
        {
            "id": b.id,
            "name": b.name,
            "refund_eligible_course_limit": b.refund_eligible_course,
            "books": books.get(b.id, []),
        }
        for b in bootcamps
    ]


# --------------------------------------------------------------------------- #
# Books
# --------------------------------------------------------------------------- #
def _book_query(
    *columns,
    name: str | None = None,
    sku: str | None = None,
    course_id: int | None = None,
    bootcamp_id: int | None = None,
    sendable: bool | None = None,
) -> Select:
    """Shared by the list and its count, so the two cannot disagree."""
    if course_id is not None and bootcamp_id is not None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="Give course_id or bootcamp_id, not both.",
        )
    statement = select(*columns).select_from(Book)
    if course_id is not None:
        statement = statement.join(CourseBook, CourseBook.book_id == Book.id).where(
            CourseBook.course_id == course_id
        )
    elif bootcamp_id is not None:
        statement = statement.join(BootcampBook, BootcampBook.book_id == Book.id).where(
            BootcampBook.bootcamp_id == bootcamp_id
        )
    if name and name.strip():
        statement = statement.where(Book.name.like(f"%{name.strip()}%"))
    if sku and sku.strip():
        statement = statement.where(Book.sku.like(f"%{sku.strip()}%"))
    if sendable is not None:
        statement = statement.where(Book.is_send_able == (1 if sendable else 0))
    return statement


def count_books(db: Session, **criteria) -> int:
    """True number of matching books -- not the capped list length."""
    statement = _book_query(func.count(Book.id), **criteria)  # validates before any query
    return db.scalar(statement) or 0


def find_books(
    db: Session,
    name: str | None = None,
    sku: str | None = None,
    course_id: int | None = None,
    bootcamp_id: int | None = None,
    sendable: bool | None = None,
    limit: int = 10,
) -> list[dict]:
    """Books by name/SKU, or the books a course or bootcamp ships."""
    columns = [Book.id, Book.name, Book.sku, Book.is_send_able]
    if course_id is not None:
        columns.append(CourseBook.delivery_start_date)
    elif bootcamp_id is not None:
        columns.append(BootcampBook.delivery_start_date)
    statement = _book_query(
        *columns, name=name, sku=sku, course_id=course_id, bootcamp_id=bootcamp_id, sendable=sendable
    )

    out = []
    for row in db.execute(statement.order_by(Book.name).limit(_cap(limit))):
        item = {"id": row[0], "name": row[1], "sku": row[2], "sendable": bool(row[3])}
        if len(row) > 4:
            item["delivery_start_date"] = row[4]
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# Book deliveries
# --------------------------------------------------------------------------- #
def build_delivery_filters(
    student_id: int | None = None,
    enrollment_id: int | None = None,
    book_id: int | None = None,
    book_name: str | None = None,
    course_id: int | None = None,
    bootcamp_id: int | None = None,
    course_type: str | None = None,
    sent: bool | None = None,
    deliverable: bool | None = None,
    additional: bool | None = None,
    manual: bool | None = None,
    country: str | None = None,
    sent_after: date | None = None,
    sent_before: date | None = None,
    created_after: date | None = None,
    created_before: date | None = None,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller."""
    d = BookDelivery
    conditions: list[ColumnElement[bool]] = []

    for column, value in (
        (d.student_id, student_id),
        (d.enrollment_id, enrollment_id),
        (d.book_id, book_id),
        (d.course_id, course_id),
        (d.bootcamp_id, bootcamp_id),
    ):
        if value is not None:
            conditions.append(column == value)

    if book_name and book_name.strip():
        conditions.append(d.book_name.like(f"%{book_name.strip()}%"))
    if course_type is not None:
        wanted = {"bootcamp": BOOTCAMP_COURSE, "course": NORMAL_COURSE}.get(course_type.lower())
        if wanted is None:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="course_type must be 'course' or 'bootcamp'.",
            )
        conditions.append(d.course_type == wanted)
    if sent is not None:
        conditions.append(d.is_sent == (1 if sent else 0))
    if deliverable is not None:
        conditions.append(d.is_deliverable == (1 if deliverable else 0))
    if additional is not None:
        conditions.append(
            d.is_additional == IS_ADDITIONAL if additional else d.is_additional != IS_ADDITIONAL
        )
    if manual is not None:
        conditions.append(d.generation_type == (1 if manual else 0))
    if country and country.strip():
        conditions.append(d.country.like(f"%{country.strip()}%"))
    if sent_after:
        conditions.append(d.sent_on >= sent_after)
    if sent_before:
        conditions.append(d.sent_on < sent_before)
    if created_after:
        conditions.append(d.created_at >= created_after)
    if created_before:
        conditions.append(d.created_at < created_before)
    return conditions


def _delivery_select(**criteria) -> Select:
    d = BookDelivery
    return select(
        d.id, d.enrollment_id, d.student_id, d.book_id, d.book_name, d.sku,
        d.course_id, d.course_or_bootcamp_name, d.course_type, d.batch,
        d.is_sent, d.sent_on, d.is_deliverable, d.is_additional,
        d.city, d.state, d.country, d.created_at,
    ).where(*build_delivery_filters(**criteria))


def search_deliveries(db: Session, limit: int = 10, **criteria) -> list[dict]:
    """Newest deliveries matching every criterion, capped at MAX_RESULTS."""
    statement = (
        _delivery_select(**criteria)
        .order_by(BookDelivery.created_at.desc(), BookDelivery.id.desc())
        .limit(_cap(limit))
    )
    rows = []
    for r in db.execute(statement):
        row = dict(r._mapping)
        row["sent"] = bool(row.pop("is_sent"))
        row["deliverable"] = bool(row.pop("is_deliverable"))
        row["additional"] = row.pop("is_additional") == IS_ADDITIONAL
        rows.append(row)
    return rows


def count_deliveries(db: Session, **criteria) -> int:
    return db.scalar(
        select(func.count(BookDelivery.id)).where(*build_delivery_filters(**criteria))
    ) or 0


def breakdown_deliveries(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Delivery counts grouped by one dimension (allow-listed)."""
    d = BookDelivery
    dimensions: dict[str, ColumnElement] = {
        "book": d.book_name,
        "sent": d.is_sent,
        "deliverable": d.is_deliverable,
        "course_type": d.course_type,
        "additional": d.is_additional,
        "country": d.country,
        "state": d.state,
        "course": d.course_or_bootcamp_name,
        "created_month": func.date_format(d.created_at, "%Y-%m"),
        "sent_month": func.date_format(d.sent_on, "%Y-%m"),
    }
    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )
    field = dimensions[by]
    is_month = by.endswith("_month")
    statement = (
        select(field.label("value"), func.count(d.id).label("count"))
        .where(*build_delivery_filters(**criteria))
        .group_by(field)
        .order_by(field.desc() if is_month else func.count(d.id).desc())
        .limit(_cap(limit))
    )
    labels = _BOOLEAN_LABELS.get(by)
    return [
        {"value": value, "count": n, **({"label": labels[bool(value)]} if labels else {})}
        for value, n in db.execute(statement)
    ]
