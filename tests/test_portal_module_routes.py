"""HTTP wiring for the feedback, catalogue, staff, extras and reference routers."""

from datetime import datetime

import pytest

from app.core.portal_database import get_portal_db
from app.main import app
from app.models.portal import catalog, feedback, staff

API = "/api"

ROUTES = [
    "/feedback/nps/summary",
    "/feedback/nps/search",
    "/feedback/nps/count",
    "/feedback/nps/breakdown?by=rating",
    "/feedback/nps-scores",
    "/packages",
    "/packages/1",
    "/bootcamps",
    "/books",
    "/book-deliveries/search",
    "/book-deliveries/count",
    "/book-deliveries/breakdown?by=sent",
    "/staff/search",
    "/staff/count",
    "/staff/breakdown?by=role",
    "/staff/1/courses",
    "/courses/1/staff",
    "/pause-log/search",
    "/pause-log/count",
    "/pause-log/breakdown?by=status",
    "/students/1/availability",
    "/students/1/notes/count",
    "/heard-about",
    "/reference/country",
]


@pytest.fixture
def portal_client(client, portal_db):
    app.dependency_overrides[get_portal_db] = lambda: portal_db
    yield client
    app.dependency_overrides.pop(get_portal_db, None)


@pytest.mark.parametrize("path", ROUTES)
def test_every_new_route_requires_a_login(client, path):
    assert client.get(f"{API}{path}").status_code in {401, 403}


def test_feedback_summary_route(portal_client, auth_headers, portal_db):
    portal_db.add_all(
        [
            feedback.NpsForm(id=1, student_id=1, enrollment_id=1, course_id=1, batch_id=1,
                             survey_type="completed", rating=10),
            feedback.NpsForm(id=2, student_id=2, enrollment_id=2, course_id=1, batch_id=1,
                             survey_type="completed", rating=2),
        ]
    )
    portal_db.commit()
    response = portal_client.get(f"{API}/feedback/nps/summary", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["responses"] == 2 and body["nps"] == 0.0


def test_feedback_route_answers_400_for_a_filter_the_kind_lacks(portal_client, auth_headers):
    response = portal_client.get(
        f"{API}/feedback/class_csat/summary?course_id=1", headers=auth_headers
    )
    assert response.status_code == 400
    assert "course_id" in response.json()["detail"]


def test_feedback_route_answers_400_for_an_unknown_kind(portal_client, auth_headers):
    assert portal_client.get(f"{API}/feedback/comments/count", headers=auth_headers).status_code == 400


def test_nps_scores_is_not_swallowed_by_the_kind_route(portal_client, auth_headers):
    response = portal_client.get(f"{API}/feedback/nps-scores?scope=course", headers=auth_headers)
    assert response.status_code == 200 and response.json() == []


def test_delivery_route_returns_no_personal_fields(portal_client, auth_headers, portal_db):
    portal_db.add(
        catalog.BookDelivery(id=1, book_name="Guide", is_sent=1, is_deliverable=1,
                             is_additional="IS NOT ADDITIONAL", generation_type=0,
                             created_at=datetime(2026, 1, 1))
    )
    portal_db.commit()
    rows = portal_client.get(f"{API}/book-deliveries/search", headers=auth_headers).json()
    assert len(rows) == 1
    assert {"student_email", "phone_number", "address", "student_name"}.isdisjoint(rows[0])


def test_bad_delivery_grouping_is_a_400(portal_client, auth_headers):
    response = portal_client.get(
        f"{API}/book-deliveries/breakdown?by=address", headers=auth_headers
    )
    assert response.status_code == 400


def test_staff_search_route_never_returns_contact_details(portal_client, auth_headers, portal_db):
    portal_db.add(staff.Staff(id=1, full_name="Asha Rao", status=1))
    portal_db.commit()
    rows = portal_client.get(f"{API}/staff/search", headers=auth_headers).json()
    assert [r["full_name"] for r in rows] == ["Asha Rao"]
    assert {"email", "phone"}.isdisjoint(rows[0])


def test_staff_status_out_of_range_is_rejected_by_validation(portal_client, auth_headers):
    assert portal_client.get(f"{API}/staff/count?status=9", headers=auth_headers).status_code == 422


def test_missing_course_and_package_are_404(portal_client, auth_headers):
    assert portal_client.get(f"{API}/courses/999/staff", headers=auth_headers).status_code == 404
    assert portal_client.get(f"{API}/packages/999", headers=auth_headers).status_code == 404


def test_pause_log_rejects_an_invented_status(portal_client, auth_headers):
    response = portal_client.get(f"{API}/pause-log/count?status=bogus", headers=auth_headers)
    assert response.status_code == 400


def test_reference_route_validates_kind(portal_client, auth_headers):
    assert portal_client.get(f"{API}/reference/students", headers=auth_headers).status_code == 400
    assert portal_client.get(f"{API}/reference/country", headers=auth_headers).status_code == 200
