"""Gemini tools for feedback, the product catalogue, staff, student extras and
reference data.

Merged into `ai_tools.TOOL_DECLARATIONS` and `ai_tools._HANDLERS`, so
`dispatch()` applies the same unknown-tool and unknown-parameter refusals to
these as to every other tool. Handlers reach the portal only through the
service modules, which is where the column allow-lists and row caps live.

Two rules specific to this file:

* a filter that does not apply to the chosen `kind` comes back to the model as
  an error naming the allowed filters, never silently dropped;
* nothing here can return free text a person typed -- comments, notes, pause
  reasons, addresses, staff email or phone. They are not mapped.
"""

from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from google.genai import types

from app.core.portal_database import PortalSessionLocal
from app.services import (
    catalog_service,
    feedback_service,
    reference_service,
    staff_service,
    student_extras_service,
)
from app.services.ai_tool_args import (
    _LIMIT,
    _TOTAL_NOTE,
    _bool,
    _int,
    _iso_date,
    _limit,
    _object,
    _parse_date,
)

_STR = types.Type.STRING
_INT = types.Type.INTEGER
_BOOL = types.Type.BOOLEAN


def _s(description: str) -> types.Schema:
    return types.Schema(type=_STR, description=description)


def _i(description: str) -> types.Schema:
    return types.Schema(type=_INT, description=description)


def _b(description: str) -> types.Schema:
    return types.Schema(type=_BOOL, description=description)


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #
_FEEDBACK_KIND = _s(
    "Which survey: 'assignment_csat' (1-5, per assignment), 'class_csat' (1-5, per live "
    "class), 'evaluator_csat' (1-5, per evaluated result), 'nps' (0-10, course survey, "
    "survey_type one_month/completed) or 'nps_v2' (0-10, interval survey)."
)

_FEEDBACK_FILTERS = {
    "student_id": _i("Numeric student id."),
    "course_id": _i("assignment_csat and nps only."),
    "batch_id": _i("assignment_csat and nps only."),
    "enrollment_id": _i("assignment_csat and nps only."),
    "assignment_id": _i("assignment_csat only."),
    "result_id": _i("evaluator_csat only."),
    "evaluator_id": _i("evaluator_csat only: the numeric staff id from find_staff."),
    "survey_type": _s("nps: 'one_month' or 'completed'. nps_v2: a day count such as '45'."),
    "min_rating": _i("Lowest rating to include."),
    "max_rating": _i("Highest rating to include."),
    "created_after": _iso_date("submitted on or after"),
    "created_before": _iso_date("submitted before"),
}

_FEEDBACK_KEYS = tuple(_FEEDBACK_FILTERS)

_DELIVERY_FILTERS = {
    "student_id": _i("Numeric student id."),
    "enrollment_id": _i("Numeric enrollment id."),
    "book_id": _i("Numeric book id from find_books."),
    "book_name": _s("Partial book name."),
    "course_id": _i("Numeric course id."),
    "bootcamp_id": _i("Numeric bootcamp id."),
    "course_type": _s("'course' or 'bootcamp'."),
    "sent": _b("true = already posted, false = not yet posted."),
    "deliverable": _b("true = can be delivered, false = cannot (e.g. no address)."),
    "additional": _b("true = an extra copy rather than the standard one."),
    "manual": _b("true = created by hand, false = created by the system."),
    "country": _s("Partial country name."),
    "sent_after": _iso_date("posted on or after"),
    "sent_before": _iso_date("posted before"),
    "created_after": _iso_date("delivery record created on or after"),
    "created_before": _iso_date("delivery record created before"),
}

_PAUSE_FILTERS = {
    "enrollment_id": _i("Numeric enrollment id."),
    "student_id": _i("Numeric student id."),
    "status": _s("paused, resume_requested, resumed or refund_eligible_paused_requested."),
    "request_source": _s("student, support or sales."),
    "paused_after": _iso_date("paused on or after"),
    "paused_before": _iso_date("paused before"),
    "open_only": _b("true = not yet resumed."),
}

DECLARATIONS: list[types.FunctionDeclaration] = [
    types.FunctionDeclaration(
        name="feedback_summary",
        description=(
            "Average rating, response count and rating spread for a survey, optionally "
            "filtered. For nps/nps_v2 also promoters (9-10), passives (7-8), detractors "
            "(0-6) and the NPS score. Use this for 'how satisfied', 'what is the NPS', "
            "'average rating'. Filters that do not apply to the kind are an error."
        ),
        parameters=_object({"kind": _FEEDBACK_KIND, **_FEEDBACK_FILTERS}, required=["kind"]),
    ),
    types.FunctionDeclaration(
        name="search_feedback",
        description=(
            "List individual survey responses, newest first (max 25), with the option "
            "reasons the student ticked. Free-text comments are not available. Use "
            "count_feedback for 'how many'."
        ),
        parameters=_object(
            {"kind": _FEEDBACK_KIND, **_FEEDBACK_FILTERS, "limit": _LIMIT}, required=["kind"]
        ),
    ),
    types.FunctionDeclaration(
        name="count_feedback",
        description="True number of survey responses matching the filters, not capped at 25.",
        parameters=_object({"kind": _FEEDBACK_KIND, **_FEEDBACK_FILTERS}, required=["kind"]),
    ),
    types.FunctionDeclaration(
        name="breakdown_feedback",
        description=(
            "Survey response counts and average rating grouped by one dimension: 'rating', "
            "'month', 'reason' (the ticked options -- why students rated as they did), "
            "'course' and 'batch_id' (assignment_csat, nps), 'survey_type' (nps kinds), "
            "'evaluator' (evaluator_csat, by name). Groups are biggest-first by default; "
            "for 'best rated' set sort='average_rating', for 'worst/lowest rated' set "
            "sort='lowest_rating', and give either a min_responses (e.g. 20) so a group "
            "with one response does not win."
        ),
        parameters=_object(
            {
                "kind": _FEEDBACK_KIND,
                "by": _s("rating, month, reason, course, batch_id, survey_type or evaluator."),
                "sort": _s(
                    "'count' (default, biggest group first), 'average_rating' (best rated "
                    "first) or 'lowest_rating' (worst rated first)."
                ),
                "min_responses": _i("Leave out groups with fewer responses than this."),
                **_FEEDBACK_FILTERS,
                "limit": _LIMIT,
            },
            required=["kind", "by"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_nps_scores",
        description=(
            "The portal's precomputed NPS per course or per bootcamp: promoters, "
            "detractors, responses and score, best first. Prefer this for 'which course "
            "has the best NPS'; use feedback_summary for a filtered or recomputed figure."
        ),
        parameters=_object(
            {
                "scope": _s("'course' or 'bootcamp'."),
                "name": _s("Partial course or bootcamp name."),
                "limit": _LIMIT,
            },
            required=["scope"],
        ),
    ),
    # ---- catalogue -------------------------------------------------------
    types.FunctionDeclaration(
        name="find_packages",
        description=(
            "Course packages (e.g. 'Master Access', 'VIP Membership') by partial name, "
            "each with the courses it includes and its duration in days. " + _TOTAL_NOTE
        ),
        parameters=_object({"name": _s("Partial package name."), "limit": _LIMIT}),
    ),
    types.FunctionDeclaration(
        name="find_bootcamps",
        description=(
            "Bootcamps by partial name, each with the books it ships and its "
            "refund_eligible_course_limit. That limit is NOT a yes/no flag: it is how many of "
            "the bootcamp's courses can carry the refund-eligible tag. Every bootcamp allows "
            "at least 1 (most have exactly 1, a few have 2), so all bootcamps are refund "
            "eligible; filter on the limit to count how many allow 2. " + _TOTAL_NOTE
        ),
        parameters=_object(
            {
                "name": _s("Partial bootcamp name."),
                "refund_course_limit": _i(
                    "Only bootcamps where exactly this many courses can be refund eligible "
                    "(1 or 2)."
                ),
                "limit": _LIMIT,
            }
        ),
    ),
    types.FunctionDeclaration(
        name="find_books",
        description=(
            "Books by name or SKU, or the books one course or bootcamp ships (with the "
            "date deliveries start). Give course_id or bootcamp_id, not both. For 'how many "
            "books can be posted' set sendable=true and read total_matching. " + _TOTAL_NOTE
        ),
        parameters=_object(
            {
                "name": _s("Partial book name."),
                "sku": _s("Partial SKU."),
                "course_id": _i("Books this course ships."),
                "bootcamp_id": _i("Books this bootcamp ships."),
                "sendable": _b("Only books that can be posted (true) or cannot (false)."),
                "limit": _LIMIT,
            }
        ),
    ),
    types.FunctionDeclaration(
        name="search_book_deliveries",
        description=(
            "Book deliveries owed to enrollments, newest first (max 25): book, course or "
            "bootcamp, sent/deliverable flags, sent date, city/state/country. Names, "
            "phones, emails and street addresses are not available."
        ),
        parameters=_object({**_DELIVERY_FILTERS, "limit": _LIMIT}),
    ),
    types.FunctionDeclaration(
        name="count_book_deliveries",
        description="True number of book deliveries matching the filters, not capped at 25.",
        parameters=_object(_DELIVERY_FILTERS),
    ),
    types.FunctionDeclaration(
        name="breakdown_book_deliveries",
        description=(
            "Book delivery counts grouped by one dimension: 'book', 'sent', 'deliverable', "
            "'course_type', 'additional', 'country', 'state', 'course', 'created_month' or "
            "'sent_month'."
        ),
        parameters=_object(
            {
                "by": _s(
                    "book, sent, deliverable, course_type, additional, country, state, "
                    "course, created_month or sent_month."
                ),
                **_DELIVERY_FILTERS,
                "limit": _LIMIT,
            },
            required=["by"],
        ),
    ),
    # ---- staff -----------------------------------------------------------
    types.FunctionDeclaration(
        name="find_staff",
        description=(
            "Portal staff (evaluators, coaches, admins -- not students) by name, role or "
            "status, with their roles. Returns names and roles only; email and phone are "
            "not available. Returns at most 25; use count_staff for totals."
        ),
        parameters=_object(
            {
                "query": _s("Partial name."),
                "role": _s("Partial role name, e.g. 'Evaluator', 'Performance Coach'."),
                "status": _i("0 disabled, 1 approved, 2 blocked, 3 pending."),
                "never_logged_in": _b("Only staff who never logged in (true) or who did (false)."),
                "limit": _LIMIT,
            }
        ),
    ),
    types.FunctionDeclaration(
        name="count_staff",
        description="True number of staff matching the filters (soft-deleted accounts excluded).",
        parameters=_object(
            {
                "query": _s("Partial name."),
                "role": _s("Partial role name."),
                "status": _i("0 disabled, 1 approved, 2 blocked, 3 pending."),
                "never_logged_in": _b("Only staff who never logged in (true) or who did (false)."),
            }
        ),
    ),
    types.FunctionDeclaration(
        name="breakdown_staff",
        description="Staff counts grouped by 'role' (one person can hold several) or 'status'.",
        parameters=_object(
            {
                "by": _s("role or status."),
                "role": _s("Partial role name."),
                "status": _i("0 disabled, 1 approved, 2 blocked, 3 pending."),
                "limit": _LIMIT,
            },
            required=["by"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_course_staff",
        description=(
            "The evaluators, instructors and mentors attached to one course. Needs the "
            "numeric course id -- use find_courses first if you only have a name."
        ),
        parameters=_object({"course_id": _i("Numeric course id.")}, required=["course_id"]),
    ),
    types.FunctionDeclaration(
        name="get_staff_courses",
        description=(
            "The courses one staff member evaluates, instructs or mentors, with their "
            "roles. Needs the numeric staff id -- use find_staff first."
        ),
        parameters=_object({"staff_id": _i("Numeric staff id.")}, required=["staff_id"]),
    ),
    # ---- student extras --------------------------------------------------
    types.FunctionDeclaration(
        name="search_pause_logs",
        description=(
            "Enrollment pause history, newest first (max 25): status, who requested it "
            "(student/support/sales), pause and resume dates. The reason text is not "
            "available. For 'who is paused right now' prefer count_enrollments with "
            "paused=true; this shows the history and requests."
        ),
        parameters=_object({**_PAUSE_FILTERS, "limit": _LIMIT}),
    ),
    types.FunctionDeclaration(
        name="count_pause_logs",
        description="True number of pause-log entries matching the filters.",
        parameters=_object(_PAUSE_FILTERS),
    ),
    types.FunctionDeclaration(
        name="breakdown_pause_logs",
        description="Pause-log counts grouped by 'status', 'request_source' or 'month'.",
        parameters=_object(
            {"by": _s("status, request_source or month."), **_PAUSE_FILTERS, "limit": _LIMIT},
            required=["by"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_student_availability",
        description=(
            "The weekly time windows one student said they are free, with timezone. "
            "Needs the numeric student id."
        ),
        parameters=_object({"student_id": _i("Numeric student id.")}, required=["student_id"]),
    ),
    types.FunctionDeclaration(
        name="heard_about_us",
        description=(
            "How students say they found LawSikho (website, YouTube, referral ...): "
            "counts per source across everyone, or one student's answers when "
            "student_id is given. The survey was reworded over time, so similar sources "
            "appear under more than one wording."
        ),
        parameters=_object(
            {"student_id": _i("One student's answers; omit for overall counts."), "limit": _LIMIT}
        ),
    ),
    types.FunctionDeclaration(
        name="count_student_notes",
        description=(
            "How many internal staff notes exist on a student and when the latest was "
            "written. The note text is not available."
        ),
        parameters=_object({"student_id": _i("Numeric student id.")}, required=["student_id"]),
    ),
    # ---- reference data --------------------------------------------------
    types.FunctionDeclaration(
        name="lookup_reference",
        description=(
            "Look up reference data by partial name: 'country' (with phone code), 'state' "
            "(optionally within a country_id), 'tag', 'course_category' (optionally under "
            "a parent_id) or 'job_role'. Returns ids and names. For 'how many states/"
            "categories/...' read total_matching. " + _TOTAL_NOTE
        ),
        parameters=_object(
            {
                "kind": _s("country, state, tag, course_category or job_role."),
                "query": _s("Partial name."),
                "country_id": _i("States only."),
                "parent_id": _i("Course categories only."),
                "limit": _LIMIT,
            },
            required=["kind"],
        ),
    ),
]


# --------------------------------------------------------------------------- #
# Argument coercion
# --------------------------------------------------------------------------- #
def _feedback_criteria(args: dict[str, Any]) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    for key in _FEEDBACK_KEYS:
        value = args.get(key)
        if key in {"created_after", "created_before"}:
            criteria[key] = _parse_date(value)
        elif key == "survey_type":
            criteria[key] = None if value in (None, "") else str(value)
        else:
            criteria[key] = _int(value)
    return criteria


_DELIVERY_BOOL = {"sent", "deliverable", "additional", "manual"}
_DELIVERY_DATE = {"sent_after", "sent_before", "created_after", "created_before"}
_DELIVERY_INT = {"student_id", "enrollment_id", "book_id", "course_id", "bootcamp_id"}


def _delivery_criteria(args: dict[str, Any]) -> dict[str, Any]:
    criteria: dict[str, Any] = {}
    for key in _DELIVERY_FILTERS:
        value = args.get(key)
        if key in _DELIVERY_BOOL:
            criteria[key] = _bool(value)
        elif key in _DELIVERY_DATE:
            criteria[key] = _parse_date(value)
        elif key in _DELIVERY_INT:
            criteria[key] = _int(value)
        else:
            criteria[key] = value
    return criteria


def _staff_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": args.get("query"),
        "role": args.get("role"),
        "status": _int(args.get("status")),
        "never_logged_in": _bool(args.get("never_logged_in")),
    }


def _pause_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "enrollment_id": _int(args.get("enrollment_id")),
        "student_id": _int(args.get("student_id")),
        "status": args.get("status"),
        "request_source": args.get("request_source"),
        "paused_after": _parse_date(args.get("paused_after")),
        "paused_before": _parse_date(args.get("paused_before")),
        "open_only": _bool(args.get("open_only")),
    }


def _run(fn) -> dict[str, Any]:
    """Run one service call in a portal session; a refusal comes back as data.

    The result goes back to Gemini as JSON, and the SDK cannot serialise a
    `date` or `datetime` -- one such value crashes the whole chat turn. So
    everything is passed through `jsonable_encoder` here, in one place, rather
    than trusting each handler to remember.
    """
    with PortalSessionLocal() as db:
        try:
            return jsonable_encoder(fn(db))
        except HTTPException as exc:
            return {"error": exc.detail}


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _feedback_summary(**args: Any) -> dict[str, Any]:
    kind = str(args.get("kind", ""))
    return _run(
        lambda db: feedback_service.feedback_summary(db, kind, **_feedback_criteria(args))
    )


def _search_feedback(**args: Any) -> dict[str, Any]:
    kind = str(args.get("kind", ""))

    def go(db):
        criteria = _feedback_criteria(args)
        rows = feedback_service.search_feedback(db, kind, _limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": feedback_service.count_feedback(db, kind, **criteria),
            "responses": rows,
        }

    return _run(go)


def _count_feedback(**args: Any) -> dict[str, Any]:
    kind = str(args.get("kind", ""))
    return _run(
        lambda db: {"count": feedback_service.count_feedback(db, kind, **_feedback_criteria(args))}
    )


def _breakdown_feedback(**args: Any) -> dict[str, Any]:
    kind = str(args.get("kind", ""))
    by = str(args.get("by", ""))
    return _run(
        lambda db: {
            "groups": feedback_service.breakdown_feedback(
                db,
                kind,
                by,
                _limit(args),
                sort=str(args.get("sort") or "count"),
                min_responses=_int(args.get("min_responses")),
                **_feedback_criteria(args),
            )
        }
    )


def _get_nps_scores(**args: Any) -> dict[str, Any]:
    return _run(
        lambda db: {
            "scores": feedback_service.nps_scores(
                db, str(args.get("scope", "")), args.get("name"), _limit(args)
            )
        }
    )


def _find_packages(**args: Any) -> dict[str, Any]:
    def go(db):
        name = args.get("name")
        rows = catalog_service.find_packages(db, name, _limit(args))
        return {
            "returned": len(rows),
            "total_matching": catalog_service.count_packages(db, name),
            "packages": rows,
        }

    return _run(go)


def _find_bootcamps(**args: Any) -> dict[str, Any]:
    def go(db):
        name = args.get("name")
        refund_limit = _int(args.get("refund_course_limit"))
        rows = catalog_service.find_bootcamps(
            db, name, _limit(args), refund_course_limit=refund_limit
        )
        return {
            "returned": len(rows),
            "total_matching": catalog_service.count_bootcamps(db, name, refund_limit),
            "bootcamps": rows,
        }

    return _run(go)


def _find_books(**args: Any) -> dict[str, Any]:
    def go(db):
        criteria = dict(
            name=args.get("name"),
            sku=args.get("sku"),
            course_id=_int(args.get("course_id")),
            bootcamp_id=_int(args.get("bootcamp_id")),
            sendable=_bool(args.get("sendable")),
        )
        rows = catalog_service.find_books(db, limit=_limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": catalog_service.count_books(db, **criteria),
            "books": rows,
        }

    return _run(go)


def _search_book_deliveries(**args: Any) -> dict[str, Any]:
    def go(db):
        criteria = _delivery_criteria(args)
        rows = catalog_service.search_deliveries(db, _limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": catalog_service.count_deliveries(db, **criteria),
            "deliveries": rows,
        }

    return _run(go)


def _count_book_deliveries(**args: Any) -> dict[str, Any]:
    return _run(lambda db: {"count": catalog_service.count_deliveries(db, **_delivery_criteria(args))})


def _breakdown_book_deliveries(**args: Any) -> dict[str, Any]:
    by = str(args.get("by", ""))
    return _run(
        lambda db: {
            "groups": catalog_service.breakdown_deliveries(
                db, by, _limit(args), **_delivery_criteria(args)
            )
        }
    )


def _find_staff(**args: Any) -> dict[str, Any]:
    def go(db):
        criteria = _staff_criteria(args)
        rows = staff_service.find_staff(db, _limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": staff_service.count_staff(db, **criteria),
            "staff": rows,
        }

    return _run(go)


def _count_staff(**args: Any) -> dict[str, Any]:
    return _run(lambda db: {"count": staff_service.count_staff(db, **_staff_criteria(args))})


def _breakdown_staff(**args: Any) -> dict[str, Any]:
    by = str(args.get("by", ""))
    criteria = _staff_criteria(args)
    return _run(
        lambda db: {"groups": staff_service.breakdown_staff(db, by, _limit(args), **criteria)}
    )


def _get_course_staff(course_id: Any) -> dict[str, Any]:
    return _run(lambda db: staff_service.course_staff(db, int(course_id)))


def _get_staff_courses(staff_id: Any) -> dict[str, Any]:
    return _run(lambda db: staff_service.staff_courses(db, int(staff_id)))


def _search_pause_logs(**args: Any) -> dict[str, Any]:
    def go(db):
        criteria = _pause_criteria(args)
        rows = student_extras_service.search_pause_logs(db, _limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": student_extras_service.count_pause_logs(db, **criteria),
            "pause_logs": rows,
        }

    return _run(go)


def _count_pause_logs(**args: Any) -> dict[str, Any]:
    return _run(
        lambda db: {"count": student_extras_service.count_pause_logs(db, **_pause_criteria(args))}
    )


def _breakdown_pause_logs(**args: Any) -> dict[str, Any]:
    by = str(args.get("by", ""))
    criteria = _pause_criteria(args)
    return _run(
        lambda db: {
            "groups": student_extras_service.breakdown_pause_logs(db, by, _limit(args), **criteria)
        }
    )


def _get_student_availability(student_id: Any) -> dict[str, Any]:
    def go(db):
        windows = student_extras_service.student_availability(db, int(student_id))
        return {"student_id": int(student_id), "count": len(windows), "windows": windows}

    return _run(go)


def _heard_about_us(**args: Any) -> dict[str, Any]:
    return _run(
        lambda db: {
            "sources": student_extras_service.heard_about(
                db, _int(args.get("student_id")), _limit(args)
            )
        }
    )


def _count_student_notes(student_id: Any) -> dict[str, Any]:
    return _run(lambda db: student_extras_service.note_count(db, int(student_id)))


def _lookup_reference(**args: Any) -> dict[str, Any]:
    def go(db):
        criteria = (
            str(args.get("kind", "")),
            args.get("query"),
            _int(args.get("country_id")),
            _int(args.get("parent_id")),
        )
        rows = reference_service.lookup(db, *criteria, _limit(args))
        return {
            "returned": len(rows),
            "total_matching": reference_service.count(db, *criteria),
            "results": rows,
        }

    return _run(go)


HANDLERS = {
    "feedback_summary": _feedback_summary,
    "search_feedback": _search_feedback,
    "count_feedback": _count_feedback,
    "breakdown_feedback": _breakdown_feedback,
    "get_nps_scores": _get_nps_scores,
    "find_packages": _find_packages,
    "find_bootcamps": _find_bootcamps,
    "find_books": _find_books,
    "search_book_deliveries": _search_book_deliveries,
    "count_book_deliveries": _count_book_deliveries,
    "breakdown_book_deliveries": _breakdown_book_deliveries,
    "find_staff": _find_staff,
    "count_staff": _count_staff,
    "breakdown_staff": _breakdown_staff,
    "get_course_staff": _get_course_staff,
    "get_staff_courses": _get_staff_courses,
    "search_pause_logs": _search_pause_logs,
    "count_pause_logs": _count_pause_logs,
    "breakdown_pause_logs": _breakdown_pause_logs,
    "get_student_availability": _get_student_availability,
    "heard_about_us": _heard_about_us,
    "count_student_notes": _count_student_notes,
    "lookup_reference": _lookup_reference,
}
