"""Guards and behaviour for packages, bootcamps, books and book deliveries."""

from datetime import date, datetime

import pytest
from fastapi import HTTPException

from app.models.portal import catalog as m
from app.models.portal.course import Course
from app.services import ai_tools, catalog_service


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


# --------------------------------------------------------------------------- #
# Column allow-lists
# --------------------------------------------------------------------------- #
PERSONAL = {
    "student_name", "student_email", "phone_number", "address", "pin_code", "student_code", "comment",
}


def test_book_delivery_maps_no_personal_columns():
    """A postal address and a phone number have no business in a prompt."""
    assert _mapped(m.BookDelivery).isdisjoint(PERSONAL), _mapped(m.BookDelivery) & PERSONAL


def test_delivery_model_keeps_city_state_country_only():
    assert {"city", "state", "country"} <= _mapped(m.BookDelivery)


def test_package_maps_no_image_or_staff_ids():
    assert _mapped(m.Package).isdisjoint({"image_path", "created_by", "updated_by"})


def test_delivery_rows_never_carry_personal_keys(portal_db):
    portal_db.add(m.BookDelivery(id=1, book_name="B", is_sent=0, is_deliverable=1,
                                 is_additional="IS NOT ADDITIONAL", generation_type=0))
    portal_db.commit()
    row = catalog_service.search_deliveries(portal_db)[0]
    assert PERSONAL.isdisjoint(row)


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def test_bad_course_type_is_a_400():
    with pytest.raises(HTTPException) as exc:
        catalog_service.build_delivery_filters(course_type="webinar")
    assert exc.value.status_code == 400


def test_breakdown_rejects_arbitrary_columns_before_querying():
    with pytest.raises(HTTPException) as exc:
        catalog_service.breakdown_deliveries(None, "student_email")
    assert exc.value.status_code == 400


def test_course_and_bootcamp_books_cannot_be_combined():
    with pytest.raises(HTTPException) as exc:
        catalog_service.find_books(None, course_id=1, bootcamp_id=2)
    assert exc.value.status_code == 400


# --------------------------------------------------------------------------- #
# Behaviour against a real (SQLite) database
# --------------------------------------------------------------------------- #
def _delivery(i, **kw):
    defaults = dict(
        id=i, book_id=1, book_name="Contract Drafting", is_sent=0, is_deliverable=1,
        is_additional="IS NOT ADDITIONAL", generation_type=0, course_type="NORMAL COURSE",
        country="India", created_at=datetime(2026, 1, i),
    )
    return m.BookDelivery(**{**defaults, **kw})


def test_delivery_filters_and_count_agree(portal_db):
    portal_db.add_all(
        [
            _delivery(1, is_sent=1, sent_on=date(2026, 2, 1)),
            _delivery(2, is_sent=0),
            _delivery(3, is_sent=1, is_additional="IS ADDITIONAL", course_type="BOOTCAMP"),
            _delivery(4, is_deliverable=0),
        ]
    )
    portal_db.commit()

    for criteria, expected in [
        ({}, 4),
        ({"sent": True}, 2),
        ({"sent": False}, 2),
        ({"additional": True}, 1),
        ({"course_type": "bootcamp"}, 1),
        ({"deliverable": False}, 1),
        ({"sent_after": date(2026, 1, 15)}, 1),
    ]:
        rows = catalog_service.search_deliveries(portal_db, limit=25, **criteria)
        assert len(rows) == expected == catalog_service.count_deliveries(portal_db, **criteria)


def test_delivery_rows_use_real_booleans(portal_db):
    portal_db.add(_delivery(1, is_sent=1, is_additional="IS ADDITIONAL"))
    portal_db.commit()
    row = catalog_service.search_deliveries(portal_db)[0]
    assert row["sent"] is True and row["additional"] is True and row["deliverable"] is True


def test_sent_breakdown_is_labelled(portal_db):
    portal_db.add_all([_delivery(1, is_sent=1), _delivery(2, is_sent=1), _delivery(3, is_sent=0)])
    portal_db.commit()
    groups = catalog_service.breakdown_deliveries(portal_db, "sent")
    assert groups == [
        {"value": 1, "count": 2, "label": "sent"},
        {"value": 0, "count": 1, "label": "not sent"},
    ]


def test_packages_list_their_courses_and_hide_deleted(portal_db):
    portal_db.add_all(
        [
            m.Package(id=1, name="Master Access", duration_days=1825),
            m.Package(id=2, name="Old Package", duration_days=30, deleted_at=datetime(2025, 1, 1)),
            Course(id=10, course_name="Contract Drafting", status=1, duration_days=90,
                   course_type=1, is_job_eligible=0),
            m.PackageCourse(id=1, package_id=1, course_id=10),
        ]
    )
    portal_db.commit()

    packages = catalog_service.find_packages(portal_db)
    assert [p["name"] for p in packages] == ["Master Access"]
    assert packages[0]["courses"] == [{"course_id": 10, "course_name": "Contract Drafting"}]
    assert catalog_service.get_package(portal_db, 1)["duration_days"] == 1825
    with pytest.raises(HTTPException) as exc:
        catalog_service.get_package(portal_db, 2)
    assert exc.value.status_code == 404


def test_books_by_course_carry_the_delivery_start_date(portal_db):
    portal_db.add_all(
        [
            m.Book(id=1, name="Drafting Guide", sku="SKU-1", is_send_able=1),
            m.Book(id=2, name="Other", sku="SKU-2", is_send_able=0),
            m.CourseBook(id=1, book_id=1, course_id=10, delivery_start_date=date(2022, 1, 1)),
        ]
    )
    portal_db.commit()

    by_course = catalog_service.find_books(portal_db, course_id=10)
    assert by_course == [
        {"id": 1, "name": "Drafting Guide", "sku": "SKU-1", "sendable": True,
         "delivery_start_date": date(2022, 1, 1)}
    ]
    assert [b["id"] for b in catalog_service.find_books(portal_db, sendable=False)] == [2]
    assert [b["id"] for b in catalog_service.find_books(portal_db, sku="SKU-")] == [1, 2]


def test_bootcamps_list_their_books(portal_db):
    portal_db.add_all(
        [
            m.Bootcamp(id=1, name="Contract Bootcamp", refund_eligible_course=1),
            m.Book(id=1, name="Guide", sku="S", is_send_able=1),
            m.BootcampBook(id=1, book_id=1, bootcamp_id=1),
        ]
    )
    portal_db.commit()
    bootcamp = catalog_service.find_bootcamps(portal_db)[0]
    # a count of courses, not a yes/no flag (see models/portal/catalog.py)
    assert bootcamp["refund_eligible_course_limit"] == 1
    assert "refund_eligible" not in bootcamp
    assert [b["book_name"] for b in bootcamp["books"]] == ["Guide"]


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
CATALOG_TOOLS = {
    "find_packages", "find_bootcamps", "find_books", "search_book_deliveries",
    "count_book_deliveries", "breakdown_book_deliveries",
}


def test_catalog_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert CATALOG_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_delivery_tools_offer_no_personal_filters():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    params = set(declared["search_book_deliveries"].parameters.properties)
    assert params.isdisjoint({"email", "phone", "address", "student_name", "pin_code"})


def test_invented_delivery_parameter_is_rejected():
    result = ai_tools.dispatch("count_book_deliveries", {"email": "a@b.c"})
    assert "error" in result and "email" in result["error"]


def test_delivery_tool_returns_bad_dimension_as_data():
    result = ai_tools.dispatch("breakdown_book_deliveries", {"by": "address"})
    assert "Cannot group by" in result["error"]
