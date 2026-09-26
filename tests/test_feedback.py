"""Guards and behaviour for the CSAT / NPS survey module."""

from datetime import datetime

import pytest
from fastapi import HTTPException

from app.models.portal import feedback as m
from app.services import ai_tools, feedback_service


def _mapped(model) -> set[str]:
    return set(model.__table__.columns.keys())


def _sql(conditions) -> str:
    return " ".join(str(c) for c in conditions)


# --------------------------------------------------------------------------- #
# Column allow-lists
# --------------------------------------------------------------------------- #
FREE_TEXT = {
    "other", "other_option", "other_opinion", "comment", "reason", "experience", "suggestions",
}

SURVEY_MODELS = [m.AssignmentCsat, m.ClassCsat, m.EvaluatorCsat, m.NpsForm, m.NpsFormV2]


@pytest.mark.parametrize("model", SURVEY_MODELS, ids=lambda x: x.__tablename__)
def test_survey_models_map_no_free_text(model):
    """Typed comments hold personal circumstances and prompt injections."""
    assert _mapped(model).isdisjoint(FREE_TEXT), _mapped(model) & FREE_TEXT


@pytest.mark.parametrize("model", SURVEY_MODELS, ids=lambda x: x.__tablename__)
def test_survey_models_declare_no_relationships(model):
    assert not list(model.__mapper__.relationships)


def test_survey_rows_never_carry_free_text_keys(portal_db):
    portal_db.add(m.AssignmentCsat(id=1, student_id=1, assignment_id=1, course_id=1, rating=4))
    portal_db.commit()
    row = feedback_service.search_feedback(portal_db, "assignment_csat")[0]
    assert FREE_TEXT.isdisjoint(row)


# --------------------------------------------------------------------------- #
# Filters and kinds
# --------------------------------------------------------------------------- #
def test_unknown_kind_is_a_400_before_any_query():
    with pytest.raises(HTTPException) as exc:
        feedback_service.count_feedback(None, "comments")
    assert exc.value.status_code == 400


def test_a_filter_the_kind_lacks_is_an_error_not_ignored():
    """Dropping course_id would turn 'class ratings for this course' into 'all of them'."""
    with pytest.raises(HTTPException) as exc:
        feedback_service.build_filters("class_csat", course_id=1)
    assert exc.value.status_code == 400
    assert "course_id" in exc.value.detail


def test_none_filters_are_not_unsupported():
    assert feedback_service.build_filters("class_csat", course_id=None, student_id=None) is not None


def test_withdrawn_class_responses_are_excluded():
    assert "status" in _sql(feedback_service.build_filters("class_csat"))
    assert "status" not in _sql(feedback_service.build_filters("assignment_csat"))


def test_nps_ignores_unanswered_surveys():
    assert "rating IS NOT NULL" in _sql(feedback_service.build_filters("nps"))


def test_breakdown_rejects_arbitrary_dimensions_before_querying():
    with pytest.raises(HTTPException) as exc:
        feedback_service.breakdown_feedback(None, "nps", "comment")
    assert exc.value.status_code == 400
    with pytest.raises(HTTPException):
        feedback_service.breakdown_feedback(None, "class_csat", "course")


def test_nps_bands_match_the_portals_own():
    from app.models.portal.codes import NPS_DETRACTOR_MAX, NPS_PROMOTER_MIN

    # Modules/NPS/Http/Traits/NPSTrait.php: <7 detractor, 7-8 passive, >8 promoter
    assert (NPS_DETRACTOR_MAX, NPS_PROMOTER_MIN) == (6, 9)


# --------------------------------------------------------------------------- #
# Behaviour against a real (SQLite) database
# --------------------------------------------------------------------------- #
def test_nps_summary_arithmetic(portal_db):
    ratings = [10, 9, 8, 3, None]  # 2 promoters, 1 passive, 1 detractor, 1 unanswered
    for i, rating in enumerate(ratings, start=1):
        portal_db.add(
            m.NpsForm(
                id=i, student_id=i, enrollment_id=i, course_id=1, batch_id=1,
                survey_type="completed", rating=rating,
            )
        )
    portal_db.commit()

    summary = feedback_service.feedback_summary(portal_db, "nps")
    assert summary["responses"] == 4
    assert (summary["promoters"], summary["passives"], summary["detractors"]) == (2, 1, 1)
    assert summary["nps"] == 25.0
    assert summary["average_rating"] == 7.5


def test_nps_summary_with_no_responses_has_no_score(portal_db):
    assert feedback_service.feedback_summary(portal_db, "nps")["nps"] is None


def test_withdrawn_class_responses_do_not_count(portal_db):
    for i, (rating, status) in enumerate([(5, "A"), (1, "D"), (4, None)], start=1):
        portal_db.add(
            m.ClassCsat(id=i, student_id=i, class_date_relation_id=1, rating=rating, status=status)
        )
    portal_db.commit()
    assert feedback_service.count_feedback(portal_db, "class_csat") == 2
    assert feedback_service.feedback_summary(portal_db, "class_csat")["average_rating"] == 4.5


def test_search_returns_ticked_reasons_and_filters(portal_db):
    portal_db.add_all(
        [
            m.AssignmentCsat(id=1, student_id=10, assignment_id=1, course_id=1, rating=5,
                             created_at=datetime(2026, 1, 2)),
            m.AssignmentCsat(id=2, student_id=11, assignment_id=1, course_id=2, rating=2,
                             created_at=datetime(2026, 1, 3)),
            m.AssignmentCsatReason(id=1, question="Relevant to real life"),
            m.AssignmentCsatReasonMap(id=1, csat_id=1, reason_id=1),
        ]
    )
    portal_db.commit()

    rows = feedback_service.search_feedback(portal_db, "assignment_csat", course_id=1)
    assert [r["id"] for r in rows] == [1]
    assert rows[0]["reasons"] == ["Relevant to real life"]

    assert feedback_service.count_feedback(portal_db, "assignment_csat", max_rating=2) == 1
    newest_first = feedback_service.search_feedback(portal_db, "assignment_csat")
    assert [r["id"] for r in newest_first] == [2, 1]


def test_breakdown_by_reason_and_month(portal_db):
    portal_db.add_all(
        [
            m.AssignmentCsat(id=1, student_id=1, assignment_id=1, course_id=1, rating=5,
                             created_at=datetime(2026, 1, 2)),
            m.AssignmentCsat(id=2, student_id=2, assignment_id=1, course_id=1, rating=3,
                             created_at=datetime(2026, 2, 2)),
            m.AssignmentCsatReason(id=1, question="Learned something"),
            m.AssignmentCsatReasonMap(id=1, csat_id=1, reason_id=1),
            m.AssignmentCsatReasonMap(id=2, csat_id=2, reason_id=1),
        ]
    )
    portal_db.commit()

    by_reason = feedback_service.breakdown_feedback(portal_db, "assignment_csat", "reason")
    assert by_reason == [{"value": "Learned something", "count": 2, "average_rating": 4.0}]
    by_month = feedback_service.breakdown_feedback(portal_db, "assignment_csat", "month")
    assert [g["value"] for g in by_month] == ["2026-02", "2026-01"]  # newest first


def test_nps_scores_are_ranked_and_scoped(portal_db):
    portal_db.add_all(
        [
            m.NpsCourseData(id=1, course_id=1, course_name="Contract Drafting", promoters=3,
                            detractors=0, total_responses=3, total_students=4, nps=100.0),
            m.NpsCourseData(id=2, course_id=2, course_name="Tax Law", promoters=1, detractors=1,
                            total_responses=4, total_students=5, nps=0.0),
        ]
    )
    portal_db.commit()
    scores = feedback_service.nps_scores(portal_db, "course")
    assert [s["name"] for s in scores] == ["Contract Drafting", "Tax Law"]
    assert feedback_service.nps_scores(portal_db, "course", "tax")[0]["id"] == 2
    with pytest.raises(HTTPException):
        feedback_service.nps_scores(portal_db, "package")


def test_lists_are_capped(portal_db):
    from app.services.portal_service import MAX_RESULTS

    portal_db.add_all(
        m.AssignmentCsat(id=i, student_id=i, assignment_id=1, course_id=1, rating=4)
        for i in range(1, MAX_RESULTS + 10)
    )
    portal_db.commit()
    assert len(feedback_service.search_feedback(portal_db, "assignment_csat", limit=500)) == MAX_RESULTS


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #
FEEDBACK_TOOLS = {
    "feedback_summary", "search_feedback", "count_feedback", "breakdown_feedback", "get_nps_scores",
}


def test_feedback_tools_are_declared_and_handled():
    declared = {f.name for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    assert FEEDBACK_TOOLS <= declared
    assert declared == set(ai_tools._HANDLERS)


def test_invented_feedback_parameter_is_rejected():
    result = ai_tools.dispatch("feedback_summary", {"kind": "nps", "course": "Contract"})
    assert "error" in result and "course" in result["error"]


def test_feedback_tool_returns_bad_kind_and_bad_filter_as_data():
    assert "Unknown feedback kind" in ai_tools.dispatch("count_feedback", {"kind": "x"})["error"]
    bad = ai_tools.dispatch("feedback_summary", {"kind": "class_csat", "course_id": 1})
    assert "course_id" in bad["error"]


def test_feedback_tool_requires_a_kind():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    for name in ("feedback_summary", "search_feedback", "count_feedback", "breakdown_feedback"):
        assert "kind" in declared[name].parameters.required


# --------------------------------------------------------------------------- #
# Ranking by rating
# --------------------------------------------------------------------------- #
def test_breakdown_sort_is_validated_before_querying():
    with pytest.raises(HTTPException) as exc:
        feedback_service.breakdown_feedback(None, "nps", "rating", sort="nps; DROP TABLE x")
    assert exc.value.status_code == 400


def test_best_rated_needs_a_floor_so_one_response_does_not_win(portal_db):
    from app.models.portal.staff import Staff

    portal_db.add_all([Staff(id=1, full_name="Steady Sam", status=1),
                       Staff(id=2, full_name="Lucky Lee", status=1)])
    # Sam: 10 responses averaging 4.8. Lee: a single 5.
    portal_db.add_all(
        m.EvaluatorCsat(id=i, student_id=i, result_id=i, evaluator_id=1, rating=5 if i <= 8 else 4)
        for i in range(1, 11)
    )
    portal_db.add(m.EvaluatorCsat(id=99, student_id=99, result_id=99, evaluator_id=2, rating=5))
    portal_db.commit()

    naive = feedback_service.breakdown_feedback(
        portal_db, "evaluator_csat", "evaluator", sort="average_rating"
    )
    assert naive[0]["value"] == "Lucky Lee"  # the trap: 5.0 on one response

    floored = feedback_service.breakdown_feedback(
        portal_db, "evaluator_csat", "evaluator", sort="average_rating", min_responses=5
    )
    assert [g["value"] for g in floored] == ["Steady Sam"]

    by_count = feedback_service.breakdown_feedback(portal_db, "evaluator_csat", "evaluator")
    assert by_count[0]["value"] == "Steady Sam"  # default ordering is unchanged


def test_lowest_rating_sort_puts_the_worst_first(portal_db):
    from app.models.portal.staff import Staff

    portal_db.add_all(
        [Staff(id=1, full_name="Good", status=1), Staff(id=2, full_name="Weak", status=1)]
    )
    portal_db.add_all(
        m.EvaluatorCsat(id=i, student_id=i, result_id=i, evaluator_id=1 if i <= 6 else 2,
                        rating=5 if i <= 6 else 2)
        for i in range(1, 13)
    )
    portal_db.commit()
    worst = feedback_service.breakdown_feedback(
        portal_db, "evaluator_csat", "evaluator", sort="lowest_rating", min_responses=5
    )
    assert [g["value"] for g in worst] == ["Weak", "Good"]


def test_breakdown_tool_offers_sort_and_floor():
    declared = {f.name: f for f in ai_tools.TOOL_DECLARATIONS.function_declarations}
    params = set(declared["breakdown_feedback"].parameters.properties)
    assert {"sort", "min_responses"} <= params
    assert "min_responses" in declared["breakdown_feedback"].description
