"""List tools must say how many matched, not just what fit in the page.

Seen for real: asked how many states India has, the model paged through letter
searches and answered 32 (there are 36); asked which bootcamps allow a refund,
it collected 70 from capped searches of a table of 146. Each of those lists
returns at most 25 rows and gave the model no total, so it guessed one.
"""

import json
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from app.core.portal_database import get_portal_db
from app.main import app
from datetime import datetime

from app.models.portal import catalog as c
from app.models.portal import reference as r
from app.models.portal.course import Course
from app.services import (
    ai_tools,
    ai_tools_extra,
    catalog_service,
    enrollment_service,
    reference_service,
)
from app.services.portal_service import MAX_RESULTS

MANY = MAX_RESULTS + 11  # comfortably past the cap


@pytest.fixture
def stocked(portal_db, monkeypatch):
    """A portal with more than one page of everything."""

    @contextmanager
    def session():
        yield portal_db

    monkeypatch.setattr(ai_tools_extra, "PortalSessionLocal", session)
    monkeypatch.setattr(ai_tools, "PortalSessionLocal", session)  # find_courses lives here

    portal_db.add_all(
        [
            c.Book(id=i, name=f"Book {i:02d}", sku=f"SKU-{i}", is_send_able=1 if i % 3 else 0)
            for i in range(1, MANY + 1)
        ]
        + [
            c.Bootcamp(id=i, name=f"Bootcamp {i:02d}", refund_eligible_course=2 if i <= 4 else 1)
            for i in range(1, MANY + 1)
        ]
        + [c.Package(id=i, name=f"Package {i:02d}", duration_days=30) for i in range(1, MANY + 1)]
        + [r.Country(id=99, short="IN", name="INDIA", common_name="India", phone_code=91)]
        + [r.State(id=i, name=f"State {i:02d}", country_id=99) for i in range(1, MANY + 1)]
        + [r.State(id=500, name="Elsewhere", country_id=99)]
        + [r.JobRole(id=i, title=f"Role {i:02d}") for i in range(1, MANY + 1)]
        + [
            Course(id=i, course_name=f"Course {i:02d}", status=1, duration_days=90,
                   course_type=1, is_job_eligible=0)
            for i in range(1, MANY + 1)
        ]
        # soft-deleted: must never be listed or counted
        + [Course(id=900, course_name="Course deleted", status=1, duration_days=90,
                  course_type=1, is_job_eligible=0, deleted_at=datetime(2025, 1, 1))]
    )
    portal_db.commit()
    return portal_db


# --------------------------------------------------------------------------- #
# Services: the list is capped, the count is not
# --------------------------------------------------------------------------- #
def test_the_list_is_capped_but_the_count_is_the_truth(stocked):
    assert len(catalog_service.find_books(stocked, limit=500)) == MAX_RESULTS
    assert catalog_service.count_books(stocked) == MANY
    assert len(catalog_service.find_bootcamps(stocked, limit=500)) == MAX_RESULTS
    assert catalog_service.count_bootcamps(stocked) == MANY
    assert len(catalog_service.find_packages(stocked, limit=500)) == MAX_RESULTS
    assert catalog_service.count_packages(stocked) == MANY
    assert len(reference_service.lookup(stocked, "state", limit=500)) == MAX_RESULTS
    assert reference_service.count(stocked, "state") == MANY + 1


def test_every_filter_applies_to_the_count_as_it_does_to_the_list(stocked):
    """One builder feeds both, so a filter cannot reach one and miss the other."""
    sendable = catalog_service.count_books(stocked, sendable=True)
    not_sendable = catalog_service.count_books(stocked, sendable=False)
    assert sendable + not_sendable == MANY and 0 < not_sendable < sendable
    assert catalog_service.count_books(stocked, name="Book 0") == 9
    # SKU-1, SKU-10 ... SKU-19: eleven, so the whole set fits on one page
    assert catalog_service.count_books(stocked, sku="SKU-1") == 11 == len(
        catalog_service.find_books(stocked, sku="SKU-1", limit=MAX_RESULTS)
    )
    assert catalog_service.count_packages(stocked, name="Package 1") == len(
        catalog_service.find_packages(stocked, name="Package 1", limit=MAX_RESULTS)
    )
    assert reference_service.count(stocked, "state", query="State 0") == 9
    assert reference_service.count(stocked, "state", country_id=99) == MANY + 1
    assert reference_service.count(stocked, "state", country_id=1234) == 0


def test_a_count_under_the_cap_equals_the_list_length(stocked):
    for kwargs in ({"name": "Book 3"}, {"sku": "SKU-2"}, {"sendable": False, "name": "Book 1"}):
        assert catalog_service.count_books(stocked, **kwargs) == len(
            catalog_service.find_books(stocked, limit=MAX_RESULTS, **kwargs)
        ), kwargs
    for kind in reference_service.KINDS:
        assert reference_service.count(stocked, kind, query="zzz-no-match") == 0
        assert reference_service.lookup(stocked, kind, query="zzz-no-match") == []


def test_a_count_of_one_kind_ignores_the_others(stocked):
    assert reference_service.count(stocked, "job_role") == MANY
    assert reference_service.count(stocked, "country") == 1
    assert reference_service.count(stocked, "tag") == 0


def test_count_validates_before_touching_the_database():
    for bad in (
        lambda: reference_service.count(None, "students"),
        lambda: reference_service.count(None, "country", country_id=1),
        lambda: reference_service.count(None, "tag", parent_id=1),
        lambda: catalog_service.count_books(None, course_id=1, bootcamp_id=2),
    ):
        with pytest.raises(HTTPException) as exc:
            bad()
        assert exc.value.status_code == 400


# --------------------------------------------------------------------------- #
# Bootcamp refund limit: a count of courses, not a yes/no flag
# --------------------------------------------------------------------------- #
def test_refund_eligible_course_is_a_limit_not_a_flag(stocked):
    """The portal reads it as `$limit` ("Only N course(s) can be refund eligible").
    Treating it as a boolean made every bootcamp look eligible and the filter
    match nothing."""
    assert catalog_service.count_bootcamps(stocked, refund_course_limit=2) == 4
    assert catalog_service.count_bootcamps(stocked, refund_course_limit=1) == MANY - 4
    assert catalog_service.count_bootcamps(stocked, refund_course_limit=0) == 0
    rows = catalog_service.find_bootcamps(stocked, refund_course_limit=2, limit=MAX_RESULTS)
    assert len(rows) == 4 and {b["refund_eligible_course_limit"] for b in rows} == {2}
    assert all("refund_eligible" not in b for b in rows)


def test_the_bootcamp_tool_explains_the_limit_and_offers_no_boolean():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    params = set(declared["find_bootcamps"].parameters.properties)
    assert "refund_course_limit" in params and "refund_eligible" not in params
    description = declared["find_bootcamps"].description
    assert "NOT a yes/no flag" in description


def test_the_old_boolean_parameter_name_is_refused():
    result = ai_tools.dispatch("find_bootcamps", {"refund_eligible": True})
    assert "error" in result and "refund_eligible" in result["error"]


# --------------------------------------------------------------------------- #
# Tools: returned vs total_matching
# --------------------------------------------------------------------------- #
LIST_TOOLS = {
    "find_courses": ("courses", {"name": "Course"}),
    "find_packages": ("packages", {}),
    "find_bootcamps": ("bootcamps", {}),
    "find_books": ("books", {}),
    "lookup_reference": ("results", {"kind": "state"}),
}


@pytest.mark.parametrize("tool", sorted(LIST_TOOLS))
def test_every_list_tool_reports_returned_and_total_matching(stocked, tool):
    key, args = LIST_TOOLS[tool]
    result = ai_tools.dispatch(tool, {**args, "limit": 500})
    json.dumps(result)  # plain JSON, as the SDK needs
    assert result["returned"] == MAX_RESULTS == len(result[key])
    assert result["total_matching"] > result["returned"]


@pytest.mark.parametrize("tool", sorted(LIST_TOOLS))
def test_total_matching_equals_returned_when_everything_fits(stocked, tool):
    key, args = LIST_TOOLS[tool]
    narrow = {"name": "zzz"} if tool != "lookup_reference" else {"query": "zzz"}
    result = ai_tools.dispatch(tool, {**args, **narrow})
    assert result["returned"] == result["total_matching"] == 0 and result[key] == []


def test_the_states_of_india_are_all_counted_even_when_only_25_are_listed(stocked):
    """The exact failure: 25 listed, 36 real, answered 32."""
    result = ai_tools.dispatch("lookup_reference", {"kind": "state", "country_id": 99, "limit": 25})
    assert result["returned"] == 25 and result["total_matching"] == MANY + 1


def test_sendable_books_can_be_counted_without_paging(stocked):
    result = ai_tools.dispatch("find_books", {"sendable": True, "limit": 1})
    assert result["returned"] == 1
    assert result["total_matching"] == catalog_service.count_books(stocked, sendable=True) > 1


def test_a_tool_error_still_comes_back_as_data(stocked):
    assert "not both" in ai_tools.dispatch("find_books", {"course_id": 1, "bootcamp_id": 2})["error"]
    assert "Unknown kind" in ai_tools.dispatch("lookup_reference", {"kind": "students"})["error"]


def test_every_list_tool_description_tells_the_model_to_use_the_total():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    for name in LIST_TOOLS:
        assert "total_matching" in declared[name].description, name
        assert "never the length" in declared[name].description, name


# --------------------------------------------------------------------------- #
# find_courses -- the older tool with the same flaw
# --------------------------------------------------------------------------- #
def test_course_list_is_capped_and_the_count_is_the_truth(stocked):
    assert len(enrollment_service.find_courses(stocked, "Course", limit=500)) == MAX_RESULTS
    assert enrollment_service.count_courses(stocked, "Course") == MANY  # not the deleted one
    assert enrollment_service.count_courses(stocked, "Course 0") == 9
    assert enrollment_service.count_courses(stocked, "zzz") == 0
    assert enrollment_service.count_courses(stocked, "  Course 0  ") == 9  # trimmed, as the list is


def test_a_soft_deleted_course_is_neither_listed_nor_counted(stocked):
    names = [c.course_name for c in enrollment_service.find_courses(stocked, "deleted", limit=25)]
    assert names == [] and enrollment_service.count_courses(stocked, "deleted") == 0


def test_a_course_count_under_the_cap_equals_the_list_length(stocked):
    for name in ("Course 1", "Course 2", "Course 3"):
        assert enrollment_service.count_courses(stocked, name) == len(
            enrollment_service.find_courses(stocked, name, limit=MAX_RESULTS)
        ), name


def test_courses_can_be_counted_by_status(stocked):
    """'How many are active' needs a filter, not a hand count of a capped page."""
    stocked.add_all(
        [
            Course(id=800 + i, course_name=f"Pending {i}", status=0, duration_days=1,
                   course_type=1, is_job_eligible=0)
            for i in range(3)
        ]
    )
    stocked.commit()
    assert enrollment_service.count_courses(stocked, "Course", status=1) == MANY
    assert enrollment_service.count_courses(stocked, "Pending", status=0) == 3
    assert enrollment_service.count_courses(stocked, "Pending", status=1) == 0
    assert enrollment_service.count_courses(stocked, "Pending") == 3  # no filter: both kinds
    listed = enrollment_service.find_courses(stocked, "Pending", limit=25, status=0)
    assert len(listed) == 3 and {c.status for c in listed} == {0}


def test_the_course_tool_filters_by_status(stocked):
    stocked.add(Course(id=850, course_name="Course pending", status=0, duration_days=1,
                       course_type=1, is_job_eligible=0))
    stocked.commit()
    pending = ai_tools.dispatch("find_courses", {"name": "Course", "status": 0})
    assert pending["total_matching"] == 1 == pending["returned"]
    assert pending["courses"][0]["status"] == "pending"
    active = ai_tools.dispatch("find_courses", {"name": "Course", "status": 1, "limit": 5})
    assert active["total_matching"] == MANY and active["returned"] == 5
    assert ai_tools.dispatch("find_courses", {"name": "Course"})["total_matching"] == MANY + 1


def test_the_course_tool_has_no_bare_count_key_that_reads_like_a_total(stocked):
    """`count` was the length of the page; a model reported it as the total."""
    result = ai_tools.dispatch("find_courses", {"name": "Course", "limit": 5})
    assert "count" not in result
    assert result["returned"] == 5 and result["total_matching"] == MANY


def test_the_course_tool_keeps_its_row_shape(stocked):
    row = ai_tools.dispatch("find_courses", {"name": "Course 01"})["courses"][0]
    assert row == {"id": 1, "course_name": "Course 01", "status": "active", "duration_days": 90}


# --------------------------------------------------------------------------- #
# REST: the body stays a list, the total is a header
# --------------------------------------------------------------------------- #
@pytest.fixture
def portal_client(client, stocked):
    app.dependency_overrides[get_portal_db] = lambda: stocked
    yield client
    app.dependency_overrides.pop(get_portal_db, None)


@pytest.mark.parametrize(
    "path,total",
    [
        ("/api/courses/search?q=Course", MANY),
        ("/api/courses/search?q=Course 0", 9),
        ("/api/packages", MANY),
        ("/api/bootcamps", MANY),
        ("/api/bootcamps?refund_course_limit=2", 4),
        ("/api/books", MANY),
        ("/api/books?sendable=false", None),
        ("/api/reference/state", MANY + 1),
        ("/api/reference/state?country_id=99", MANY + 1),
        ("/api/reference/job_role?query=Role 0", 9),
    ],
)
def test_list_endpoints_put_the_total_in_a_header(portal_client, auth_headers, path, total):
    response = portal_client.get(f"{path}{'&' if '?' in path else '?'}limit=25", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list) and len(body) <= MAX_RESULTS
    header = int(response.headers["x-total-count"])
    if total is not None:
        assert header == total
    assert header >= len(body)


def test_the_course_endpoint_filters_by_status_and_validates_it(portal_client, auth_headers):
    ok = portal_client.get("/api/courses/search?q=Course&status=1&limit=5", headers=auth_headers)
    assert ok.status_code == 200 and int(ok.headers["x-total-count"]) == MANY
    none = portal_client.get("/api/courses/search?q=Course&status=0", headers=auth_headers)
    assert none.status_code == 200 and int(none.headers["x-total-count"]) == 0
    bad = portal_client.get("/api/courses/search?q=Course&status=7", headers=auth_headers)
    assert bad.status_code == 422


def test_the_header_exceeds_the_body_when_the_page_is_full(portal_client, auth_headers):
    response = portal_client.get("/api/reference/state?limit=5", headers=auth_headers)
    assert len(response.json()) == 5 and int(response.headers["x-total-count"]) == MANY + 1


def test_bad_input_is_still_a_400_on_the_list_endpoints(portal_client, auth_headers):
    assert portal_client.get("/api/reference/students", headers=auth_headers).status_code == 400
    assert portal_client.get(
        "/api/books?course_id=1&bootcamp_id=2", headers=auth_headers
    ).status_code == 400
