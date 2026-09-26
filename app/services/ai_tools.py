"""The portal lookups Gemini is allowed to call.

Two halves that must stay in step:

* `TOOL_DECLARATIONS` -- what the model is told it can call;
* `dispatch()` -- what actually runs, which only ever reaches the portal
  through the service modules, so the allow-listed columns and row caps there
  apply to the model exactly as they do to the REST endpoints.

The model chooses the arguments, never the SQL. A function or parameter name
it invents is refused here rather than reaching the database.
"""

import logging
from typing import Any

from fastapi import HTTPException
from google.genai import types
from sqlalchemy.exc import OperationalError

from app.core.config import settings
from app.core.portal_database import PortalSessionLocal
from app.schemas.assignment import (
    assignment_out,
    assignment_summary,
    student_assignment_summary,
)
from app.schemas.completion import completion_out, completion_summary
from app.schemas.enrollment import enrollment_out, enrollment_summary
from app.schemas.result import result_out, result_summary
from app.schemas.student import StudentOut, StudentSummary
from app.services import (
    ai_tools_extra,
    assignment_service,
    completion_service,
    enrollment_service,
    portal_service,
    result_service,
)

from app.services.ai_tool_args import (  # noqa: E402 -- shared with ai_tools_extra
    _LIMIT,
    _TOTAL_NOTE,
    _bool,
    _float,
    _int,
    _iso_date,
    _limit,
    _object,
    _parse_date,
)

logger = logging.getLogger(__name__)

# MariaDB's error for a statement stopped by max_statement_time.
_STATEMENT_TIMEOUT_CODE = 1969


# --------------------------------------------------------------------------- #
# Students
# --------------------------------------------------------------------------- #
_STUDENT_FILTERS = {
    "query": types.Schema(
        type=types.Type.STRING,
        description="Free text matched against full name, email, or registration code.",
    ),
    "city": types.Schema(type=types.Type.STRING, description="City, partial match."),
    "state": types.Schema(type=types.Type.STRING, description="State, partial match."),
    "country": types.Schema(
        type=types.Type.STRING,
        description="Country, partial match. Most students are in India; many rows have no country set.",
    ),
    "status": types.Schema(
        type=types.Type.INTEGER,
        description="Student account status: 0 = pending, 1 = active, 2 = disabled. Omit for all.",
    ),
    "current_activity": types.Schema(
        type=types.Type.STRING,
        description=(
            "What the student is doing currently, partial match, e.g. 'Law Student', "
            "'In-house lawyer', 'Independent Litigator'. Only set for a minority of rows."
        ),
    ),
    "registered_after": _iso_date("registered on or after"),
    "registered_before": _iso_date("registered before"),
    "last_login_after": _iso_date("last logged in on or after"),
    "never_logged_in": types.Schema(
        type=types.Type.BOOLEAN, description="True for students who have never logged in."
    ),
}

# --------------------------------------------------------------------------- #
# Enrollments
# --------------------------------------------------------------------------- #
_ENROLLMENT_FILTERS = {
    "student_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one student."),
    "course_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course."),
    "course_name": types.Schema(
        type=types.Type.STRING, description="Course name, partial match, e.g. 'Contract Drafting'."
    ),
    "batch_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course batch."),
    "status": types.Schema(
        type=types.Type.INTEGER,
        description=(
            "Current enrollment status: 0 = pending, 1 = active, 2 = paused, "
            "3 = resume requested, 4 = pause requested (refund eligible). Omit for all."
        ),
    ),
    "enrollment_type": types.Schema(
        type=types.Type.INTEGER,
        description="How the student enrolled: 1 = normal, 2 = package, 3 = bootcamp, 4 = package batch.",
    ),
    "completed": types.Schema(type=types.Type.BOOLEAN, description="True = course completed."),
    "is_certified": types.Schema(type=types.Type.BOOLEAN, description="True = certificate issued."),
    "paused": types.Schema(
        type=types.Type.BOOLEAN, description="True = currently paused (same as status 2)."
    ),
    "enrolled_after": _iso_date("enrolled on or after"),
    "enrolled_before": _iso_date("enrolled before"),
    "expiring_before": _iso_date("course access expires before"),
    "min_progress": types.Schema(
        type=types.Type.NUMBER, description="Minimum course progress percent, 0-100."
    ),
    "max_progress": types.Schema(
        type=types.Type.NUMBER, description="Maximum course progress percent, 0-100."
    ),
}

# --------------------------------------------------------------------------- #
# Assignments -- the definitions a course sets
# --------------------------------------------------------------------------- #
_ASSIGNMENT_FILTERS = {
    "course_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course."),
    "course_name": types.Schema(
        type=types.Type.STRING, description="Course name, partial match, e.g. 'Contract Drafting'."
    ),
    "topic": types.Schema(
        type=types.Type.STRING, description="Syllabus topic title, partial match."
    ),
    "assignment_code": types.Schema(
        type=types.Type.STRING, description="Assignment code, partial match."
    ),
    "assignment_type": types.Schema(
        type=types.Type.INTEGER,
        description=(
            "0 = subjective (exercises), 1 = written assignment. Note 0 is a real type, "
            "not 'unset': about half of all assignments are subjective."
        ),
    ),
    "status": types.Schema(
        type=types.Type.INTEGER,
        description="Assignment status: 0 = deactivated, 1 = active. Omit for all.",
    ),
    "plagiarism_checked": types.Schema(
        type=types.Type.BOOLEAN, description="True = the assignment is plagiarism-checked."
    ),
    "min_exercises": types.Schema(
        type=types.Type.INTEGER, description="Minimum number of exercises."
    ),
    "max_exercises": types.Schema(
        type=types.Type.INTEGER, description="Maximum number of exercises."
    ),
    "created_after": _iso_date("assignment created on or after"),
    "created_before": _iso_date("assignment created before"),
}

# --------------------------------------------------------------------------- #
# Student assignments -- what one student was given
# --------------------------------------------------------------------------- #
_STUDENT_ASSIGNMENT_FILTERS = {
    "enrollment_id": types.Schema(
        type=types.Type.INTEGER, description="Portal id of one enrollment."
    ),
    "student_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one student."),
    "assignment_id": types.Schema(
        type=types.Type.INTEGER, description="Portal id of one assignment definition."
    ),
    "course_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course."),
    "course_name": types.Schema(type=types.Type.STRING, description="Course name, partial match."),
    "status": types.Schema(
        type=types.Type.INTEGER,
        description=(
            "0 = deactivated, 1 = active (switched on, nothing handed in), 2 = pending, "
            "3 = submitted, 4 = resubmitted, 5 = evaluated. Most rows are 1."
        ),
    ),
    "submitted": types.Schema(
        type=types.Type.BOOLEAN,
        description=(
            "True = the student has handed something in (status 3, 4 or 5). Use this "
            "rather than status for 'how many have submitted' -- status 1 means the "
            "assignment is merely switched on, which is most of the table."
        ),
    ),
    "mandatory": types.Schema(type=types.Type.BOOLEAN, description="True = mandatory assignment."),
    "overdue": types.Schema(
        type=types.Type.BOOLEAN,
        description="True = deadline has passed with nothing handed in.",
    ),
    "due_after": _iso_date("submission deadline on or after"),
    "due_before": _iso_date("submission deadline before"),
    "min_submits": types.Schema(
        type=types.Type.INTEGER, description="Minimum number of submission attempts."
    ),
}

# --------------------------------------------------------------------------- #
# Results -- submissions and their evaluations
# --------------------------------------------------------------------------- #
_RESULT_FILTERS = {
    "student_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one student."),
    "student_assignment_id": types.Schema(
        type=types.Type.INTEGER,
        description="Portal id of one student assignment (not an assignment definition id).",
    ),
    "enrollment_id": types.Schema(
        type=types.Type.INTEGER, description="Portal id of one enrollment."
    ),
    "course_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course."),
    "course_name": types.Schema(type=types.Type.STRING, description="Course name, partial match."),
    "status": types.Schema(
        type=types.Type.INTEGER,
        description=(
            "0 = deactivated, 1 = active (awaiting evaluation), 2 = pending, "
            "3 = resubmission requested, 5 = evaluated. There is no 4."
        ),
    ),
    "evaluated": types.Schema(type=types.Type.BOOLEAN, description="True = evaluated (status 5)."),
    "latest_only": types.Schema(
        type=types.Type.BOOLEAN,
        description=(
            "True = only the current submission of each assignment. A resubmission "
            "supersedes the row before it, so counting every row answers 'how many "
            "submissions' while latest_only answers 'how many assignments'."
        ),
    ),
    "awaiting_evaluation": types.Schema(
        type=types.Type.BOOLEAN, description="True = submitted and not yet evaluated."
    ),
    "overdue_evaluation": types.Schema(
        type=types.Type.BOOLEAN,
        description="True = past the evaluation due date and still unevaluated.",
    ),
    "plagiarism_flagged": types.Schema(
        type=types.Type.BOOLEAN, description="True = a non-zero plagiarism result."
    ),
    "ai_evaluated": types.Schema(
        type=types.Type.BOOLEAN, description="True = the AI evaluator has scored it."
    ),
    "min_score": types.Schema(
        type=types.Type.NUMBER,
        description="Minimum score (the reviewer's edited score when set, AI score otherwise).",
    ),
    "max_score": types.Schema(type=types.Type.NUMBER, description="Maximum score."),
    "submitted_after": _iso_date("submitted on or after"),
    "submitted_before": _iso_date("submitted before"),
    "evaluated_after": _iso_date("evaluated on or after"),
}

# --------------------------------------------------------------------------- #
# Course completion
# --------------------------------------------------------------------------- #
_COMPLETION_FILTERS = {
    "student_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one student."),
    "course_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course."),
    "course_name": types.Schema(type=types.Type.STRING, description="Course name, partial match."),
    "batch_id": types.Schema(type=types.Type.INTEGER, description="Portal id of one course batch."),
    "state": types.Schema(
        type=types.Type.STRING,
        description=(
            "The completion verdict: 'completed', 'awaiting_mcq' or 'not_completed'. "
            "This is not the raw completed column: an enrollment with completed = 1 on "
            "a course that requires an LMS MCQ is 'awaiting_mcq' -- not finished -- "
            "until the MCQ is confirmed. Prefer these completion tools over the "
            "enrollment tools' `completed` filter for any question about students "
            "finishing a course."
        ),
    ),
    "mcq_required": types.Schema(
        type=types.Type.BOOLEAN, description="True = the course requires an LMS MCQ to finish."
    ),
    "is_certified": types.Schema(type=types.Type.BOOLEAN, description="True = certificate issued."),
    "min_percent": types.Schema(
        type=types.Type.NUMBER, description="Minimum overall progress percent, 0-100."
    ),
    "max_percent": types.Schema(
        type=types.Type.NUMBER, description="Maximum overall progress percent, 0-100."
    ),
    "completed_after": _iso_date("marked complete on or after"),
    "completed_before": _iso_date("marked complete before"),
    "certified_after": _iso_date("certified on or after"),
    "expiring_before": _iso_date("course access expires before"),
}

TOOL_DECLARATIONS = types.Tool(
    function_declarations=[
        # ---- students ------------------------------------------------------
        types.FunctionDeclaration(
            name="search_students",
            description=(
                "List students matching any combination of criteria: free text (name, "
                "email, registration code), city, state, country, account status, "
                "current occupation, registration dates, or login activity. Returns at "
                "most 25 rows, newest first. Use count_students for 'how many'."
            ),
            parameters=_object({**_STUDENT_FILTERS, "limit": _LIMIT}),
        ),
        types.FunctionDeclaration(
            name="count_students",
            description=(
                "Count students matching any combination of the student criteria. The "
                "true total, not capped at 25. With no criteria it counts every student."
            ),
            parameters=_object(_STUDENT_FILTERS),
        ),
        types.FunctionDeclaration(
            name="breakdown_students",
            description=(
                "Student counts grouped by one column -- 'country', 'state', 'city', "
                "'status' or 'current_activity'."
            ),
            parameters=_object(
                {
                    "by": types.Schema(
                        type=types.Type.STRING,
                        description="Column to group by: country, state, city, status, current_activity.",
                    ),
                    "country": _STUDENT_FILTERS["country"],
                    "status": _STUDENT_FILTERS["status"],
                    "limit": _LIMIT,
                },
                required=["by"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_student",
            description=(
                "Full profile of one student by numeric portal id: location, occupation, "
                "registration and last-login dates. Search first if you only have a name."
            ),
            parameters=_object(
                {"student_id": types.Schema(type=types.Type.INTEGER, description="Numeric portal id.")},
                required=["student_id"],
            ),
        ),
        # ---- enrollments ---------------------------------------------------
        types.FunctionDeclaration(
            name="get_student_enrollments",
            description=(
                "Every course one student is enrolled in, with status, progress, "
                "completion and certification. Needs the numeric student id -- use "
                "search_students first if you only have a name or email."
            ),
            parameters=_object(
                {"student_id": types.Schema(type=types.Type.INTEGER, description="Numeric portal id.")},
                required=["student_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="search_enrollments",
            description=(
                "List enrollments matching any combination of criteria: student, course "
                "(id or partial name), batch, status, enrollment type, completion, "
                "certification, pause state, enrollment dates, expiry, or progress. "
                "Returns at most 25 rows, newest first, plus the true total. Use "
                "count_enrollments when only the number is needed."
            ),
            parameters=_object({**_ENROLLMENT_FILTERS, "limit": _LIMIT}),
        ),
        types.FunctionDeclaration(
            name="count_enrollments",
            description=(
                "Count enrollments matching any combination of the enrollment criteria. "
                "The true total, not capped. Prefer it for every 'how many' question "
                "about enrollments, completions, certificates, or pauses."
            ),
            parameters=_object(_ENROLLMENT_FILTERS),
        ),
        types.FunctionDeclaration(
            name="breakdown_enrollments",
            description=(
                "Enrollment counts grouped by one dimension -- 'course', 'status', 'type', "
                "'completed', 'is_certified', 'batch' or 'month' (YYYY-MM, newest first). "
                "Accepts the enrollment criteria to narrow what is grouped, e.g. monthly "
                "enrollments for one course, or statuses among bootcamp enrollments."
            ),
            parameters=_object(
                {
                    "by": types.Schema(
                        type=types.Type.STRING,
                        description="course, status, type, completed, is_certified, batch, month.",
                    ),
                    **_ENROLLMENT_FILTERS,
                    "limit": types.Schema(
                        type=types.Type.INTEGER, description="Groups to return (1-25, default 10)."
                    ),
                },
                required=["by"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_enrollment",
            description="Full detail of one enrollment by its numeric portal id.",
            parameters=_object(
                {"enrollment_id": types.Schema(type=types.Type.INTEGER, description="Numeric enrollment id.")},
                required=["enrollment_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="find_courses",
            description=(
                "Find courses by partial name, returning ids, full names, status and "
                "duration. Use it to resolve a course a person names loosely before "
                "filtering enrollments by course_id, or for 'how many courses are called "
                "...'. " + _TOTAL_NOTE
            ),
            parameters=_object(
                {
                    "name": types.Schema(type=types.Type.STRING, description="Partial course name."),
                    "status": types.Schema(
                        type=types.Type.INTEGER,
                        description="1 = only active courses, 0 = only pending ones. Use it, "
                        "with total_matching, for 'how many ... are active'.",
                    ),
                    "limit": _LIMIT,
                },
                required=["name"],
            ),
        ),
        # ---- assignments ---------------------------------------------------
        types.FunctionDeclaration(
            name="search_assignments",
            description=(
                "List the assignments a course sets -- the definitions, not anything a "
                "student did. Filter by course, topic, code, type (subjective or "
                "written), status, plagiarism checking, size or creation date. Returns "
                "at most 25 rows. Use count_assignments for 'how many'."
            ),
            parameters=_object({**_ASSIGNMENT_FILTERS, "limit": _LIMIT}),
        ),
        types.FunctionDeclaration(
            name="count_assignments",
            description=(
                "Count assignment definitions matching any combination of the "
                "assignment criteria. The true total, not capped."
            ),
            parameters=_object(_ASSIGNMENT_FILTERS),
        ),
        types.FunctionDeclaration(
            name="breakdown_assignments",
            description=(
                "Assignment counts grouped by one dimension -- 'course', 'topic', "
                "'type', 'status', 'plagiarism' or 'month' (YYYY-MM, newest first)."
            ),
            parameters=_object(
                {
                    "by": types.Schema(
                        type=types.Type.STRING,
                        description="course, topic, type, status, plagiarism, month.",
                    ),
                    **_ASSIGNMENT_FILTERS,
                    "limit": types.Schema(
                        type=types.Type.INTEGER, description="Groups to return (1-25, default 10)."
                    ),
                },
                required=["by"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_assignment",
            description="Full detail of one assignment definition by its numeric portal id.",
            parameters=_object(
                {
                    "assignment_id": types.Schema(
                        type=types.Type.INTEGER, description="Numeric assignment id."
                    )
                },
                required=["assignment_id"],
            ),
        ),
        # ---- student assignments -------------------------------------------
        types.FunctionDeclaration(
            name="search_student_assignments",
            description=(
                "List assignments as handed to students -- one row per enrollment per "
                "assignment, with deadline, submission state and attempt count. Filter "
                "by student, enrollment, course, status, whether it was submitted, "
                "whether it is mandatory or overdue. Returns at most 25 rows."
            ),
            parameters=_object({**_STUDENT_ASSIGNMENT_FILTERS, "limit": _LIMIT}),
        ),
        types.FunctionDeclaration(
            name="count_student_assignments",
            description=(
                "Count student assignments matching any combination of the criteria. "
                "The true total, not capped. Use it for 'how many assignments are "
                "overdue' or 'how many has this student submitted'."
            ),
            parameters=_object(_STUDENT_ASSIGNMENT_FILTERS),
        ),
        types.FunctionDeclaration(
            name="breakdown_student_assignments",
            description=(
                "Student-assignment counts grouped by one dimension -- 'status', "
                "'course', 'mandatory', 'submit_counter', 'topic' or 'month'."
            ),
            parameters=_object(
                {
                    "by": types.Schema(
                        type=types.Type.STRING,
                        description="status, course, mandatory, submit_counter, topic, month.",
                    ),
                    **_STUDENT_ASSIGNMENT_FILTERS,
                    "limit": types.Schema(
                        type=types.Type.INTEGER, description="Groups to return (1-25, default 10)."
                    ),
                },
                required=["by"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_enrollment_assignments",
            description=(
                "Every assignment for one enrollment, in the order the student sees "
                "them, with deadlines and submission state. Needs the numeric "
                "enrollment id -- use get_student_enrollments first if you only have a "
                "student."
            ),
            parameters=_object(
                {
                    "enrollment_id": types.Schema(
                        type=types.Type.INTEGER, description="Numeric enrollment id."
                    )
                },
                required=["enrollment_id"],
            ),
        ),
        # ---- results -------------------------------------------------------
        types.FunctionDeclaration(
            name="search_results",
            description=(
                "List submissions with their evaluation state and scores. Filter by "
                "student, enrollment, course, status, whether evaluated, whether "
                "awaiting or overdue for evaluation, plagiarism, AI evaluation, or "
                "score range. Returns at most 25 rows, newest first. No feedback text "
                "is ever returned."
            ),
            parameters=_object({**_RESULT_FILTERS, "limit": _LIMIT}),
        ),
        types.FunctionDeclaration(
            name="count_results",
            description=(
                "Count submissions matching any combination of the result criteria. The "
                "true total, not capped. This counts submissions: pass latest_only=true "
                "to count assignments instead, since a resubmitted assignment has "
                "several rows."
            ),
            parameters=_object(_RESULT_FILTERS),
        ),
        types.FunctionDeclaration(
            name="breakdown_results",
            description=(
                "Result counts grouped by one dimension -- 'status', 'course', "
                "'plagiarism', 'review_done', 'ai_evaluation_status', "
                "'assignment_type', 'submitted_month' or 'evaluated_month'."
            ),
            parameters=_object(
                {
                    "by": types.Schema(
                        type=types.Type.STRING,
                        description=(
                            "status, course, plagiarism, review_done, ai_evaluation_status, "
                            "assignment_type, submitted_month, evaluated_month."
                        ),
                    ),
                    **_RESULT_FILTERS,
                    "limit": types.Schema(
                        type=types.Type.INTEGER, description="Groups to return (1-25, default 10)."
                    ),
                },
                required=["by"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_result",
            description=(
                "Full detail of one submission by its numeric portal id: status, "
                "scores, evaluation and review dates. No feedback text."
            ),
            parameters=_object(
                {"result_id": types.Schema(type=types.Type.INTEGER, description="Numeric result id.")},
                required=["result_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_student_results",
            description=(
                "One student's current submissions, newest first, with scores and "
                "evaluation state. Superseded resubmissions are left out. Needs the "
                "numeric student id."
            ),
            parameters=_object(
                {"student_id": types.Schema(type=types.Type.INTEGER, description="Numeric portal id.")},
                required=["student_id"],
            ),
        ),
        # ---- course completion ---------------------------------------------
        types.FunctionDeclaration(
            name="completion_summary",
            description=(
                "How many enrollments are completed, awaiting LMS MCQ confirmation, or "
                "not completed, plus how many are certified -- in one answer whose parts "
                "add up to the total. The first tool to reach for on any question about "
                "how many students have finished a course, because it separates "
                "genuinely finished from 'passed the marks criteria but the MCQ is "
                "unconfirmed', which the raw completed flag does not."
            ),
            parameters=_object(_COMPLETION_FILTERS),
        ),
        types.FunctionDeclaration(
            name="search_completions",
            description=(
                "List enrollments with their completion verdict, progress percent, MCQ "
                "state and certification. Filter by student, course, batch, state "
                "('completed', 'awaiting_mcq', 'not_completed'), certification or "
                "progress. Returns at most 25 rows."
            ),
            parameters=_object({**_COMPLETION_FILTERS, "limit": _LIMIT}),
        ),
        types.FunctionDeclaration(
            name="count_completions",
            description=(
                "Count enrollments matching any combination of the completion criteria. "
                "The true total, not capped. Pass state='completed' for genuinely "
                "finished courses."
            ),
            parameters=_object(_COMPLETION_FILTERS),
        ),
        types.FunctionDeclaration(
            name="breakdown_completions",
            description=(
                "Completion counts grouped by one dimension -- 'state', 'course', "
                "'mcq_required', 'is_certified' or 'completed_month'."
            ),
            parameters=_object(
                {
                    "by": types.Schema(
                        type=types.Type.STRING,
                        description="state, course, mcq_required, is_certified, completed_month.",
                    ),
                    **_COMPLETION_FILTERS,
                    "limit": types.Schema(
                        type=types.Type.INTEGER, description="Groups to return (1-25, default 10)."
                    ),
                },
                required=["by"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_completion",
            description=(
                "One enrollment's completion in full: the verdict, both passing "
                "percentages, MCQ state, certification, and how much coursework has "
                "been handed in."
            ),
            parameters=_object(
                {
                    "enrollment_id": types.Schema(
                        type=types.Type.INTEGER, description="Numeric enrollment id."
                    )
                },
                required=["enrollment_id"],
            ),
        ),
        types.FunctionDeclaration(
            name="get_student_completion",
            description=(
                "Completion state for every course one student is enrolled in. Needs "
                "the numeric student id -- use search_students first if you only have a "
                "name or email."
            ),
            parameters=_object(
                {"student_id": types.Schema(type=types.Type.INTEGER, description="Numeric portal id.")},
                required=["student_id"],
            ),
        ),
    ]
)


# --------------------------------------------------------------------------- #
# Argument coercion -- every value here was written by the model
# --------------------------------------------------------------------------- #


def _student_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "query": args.get("query"),
        "city": args.get("city"),
        "state": args.get("state"),
        "country": args.get("country"),
        "status": _int(args.get("status")),
        "current_activity": args.get("current_activity"),
        "registered_after": _parse_date(args.get("registered_after")),
        "registered_before": _parse_date(args.get("registered_before")),
        "last_login_after": _parse_date(args.get("last_login_after")),
        "never_logged_in": _bool(args.get("never_logged_in")),
    }


def _enrollment_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "student_id": _int(args.get("student_id")),
        "course_id": _int(args.get("course_id")),
        "course_name": args.get("course_name"),
        "batch_id": _int(args.get("batch_id")),
        "status": _int(args.get("status")),
        "enrollment_type": _int(args.get("enrollment_type")),
        "completed": _bool(args.get("completed")),
        "is_certified": _bool(args.get("is_certified")),
        "paused": _bool(args.get("paused")),
        "enrolled_after": _parse_date(args.get("enrolled_after")),
        "enrolled_before": _parse_date(args.get("enrolled_before")),
        "expiring_before": _parse_date(args.get("expiring_before")),
        "min_progress": _float(args.get("min_progress")),
        "max_progress": _float(args.get("max_progress")),
    }


def _assignment_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "course_id": _int(args.get("course_id")),
        "course_name": args.get("course_name"),
        "topic": args.get("topic"),
        "assignment_code": args.get("assignment_code"),
        "assignment_type": _int(args.get("assignment_type")),
        "status": _int(args.get("status")),
        "plagiarism_checked": _bool(args.get("plagiarism_checked")),
        "min_exercises": _int(args.get("min_exercises")),
        "max_exercises": _int(args.get("max_exercises")),
        "created_after": _parse_date(args.get("created_after")),
        "created_before": _parse_date(args.get("created_before")),
    }


def _student_assignment_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "enrollment_id": _int(args.get("enrollment_id")),
        "student_id": _int(args.get("student_id")),
        "assignment_id": _int(args.get("assignment_id")),
        "course_id": _int(args.get("course_id")),
        "course_name": args.get("course_name"),
        "status": _int(args.get("status")),
        "submitted": _bool(args.get("submitted")),
        "mandatory": _bool(args.get("mandatory")),
        "overdue": _bool(args.get("overdue")),
        "due_after": _parse_date(args.get("due_after")),
        "due_before": _parse_date(args.get("due_before")),
        "min_submits": _int(args.get("min_submits")),
    }


def _result_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "student_id": _int(args.get("student_id")),
        "student_assignment_id": _int(args.get("student_assignment_id")),
        "enrollment_id": _int(args.get("enrollment_id")),
        "course_id": _int(args.get("course_id")),
        "course_name": args.get("course_name"),
        "status": _int(args.get("status")),
        "evaluated": _bool(args.get("evaluated")),
        "latest_only": _bool(args.get("latest_only")),
        "awaiting_evaluation": _bool(args.get("awaiting_evaluation")),
        "overdue_evaluation": _bool(args.get("overdue_evaluation")),
        "plagiarism_flagged": _bool(args.get("plagiarism_flagged")),
        "ai_evaluated": _bool(args.get("ai_evaluated")),
        "min_score": _float(args.get("min_score")),
        "max_score": _float(args.get("max_score")),
        "submitted_after": _parse_date(args.get("submitted_after")),
        "submitted_before": _parse_date(args.get("submitted_before")),
        "evaluated_after": _parse_date(args.get("evaluated_after")),
    }


def _completion_criteria(args: dict[str, Any]) -> dict[str, Any]:
    return {
        "student_id": _int(args.get("student_id")),
        "course_id": _int(args.get("course_id")),
        "course_name": args.get("course_name"),
        "batch_id": _int(args.get("batch_id")),
        # Left as written: an unknown state must be refused by the service, not
        # silently dropped, or the answer would cover every enrollment.
        "state": args.get("state"),
        "mcq_required": _bool(args.get("mcq_required")),
        "is_certified": _bool(args.get("is_certified")),
        "min_percent": _float(args.get("min_percent")),
        "max_percent": _float(args.get("max_percent")),
        "completed_after": _parse_date(args.get("completed_after")),
        "completed_before": _parse_date(args.get("completed_before")),
        "certified_after": _parse_date(args.get("certified_after")),
        "expiring_before": _parse_date(args.get("expiring_before")),
    }


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #
def _search_students(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        criteria = _student_criteria(args)
        rows = portal_service.search_students(db, limit=_limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": portal_service.count_students(db, **criteria),
            "students": [StudentSummary.model_validate(r).model_dump(mode="json") for r in rows],
        }


def _count_students(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        return {"count": portal_service.count_students(db, **_student_criteria(args))}


def _breakdown_students(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            groups = portal_service.group_students(
                db,
                str(args.get("by", "")),
                limit=_limit(args),
                country=args.get("country"),
                status=_int(args.get("status")),
            )
        except HTTPException as exc:
            return {"error": exc.detail}
        return {"groups": groups}


def _get_student(student_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            student = portal_service.get_student(db, int(student_id))
        except HTTPException:
            return {"error": "No student with that id."}
        return StudentOut.model_validate(student).model_dump(mode="json")


def _get_student_enrollments(student_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        rows = enrollment_service.student_enrollments(db, int(student_id))
        if not rows:
            # Could be a student with no enrollments or no such student; say
            # which, so the model does not guess.
            try:
                portal_service.get_student(db, int(student_id))
            except HTTPException:
                return {"error": "No student with that id."}
        return {
            "student_id": int(student_id),
            "count": len(rows),
            "enrollments": [enrollment_summary(e).model_dump(mode="json") for e in rows],
        }


def _search_enrollments(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        criteria = _enrollment_criteria(args)
        rows = enrollment_service.list_enrollments(db, limit=_limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": enrollment_service.count_enrollments(db, **criteria),
            "enrollments": [enrollment_summary(e).model_dump(mode="json") for e in rows],
        }


def _count_enrollments(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        return {"count": enrollment_service.count_enrollments(db, **_enrollment_criteria(args))}


def _breakdown_enrollments(**args: Any) -> dict[str, Any]:
    by = str(args.pop("by", ""))
    limit = _limit(args)
    args.pop("limit", None)
    with PortalSessionLocal() as db:
        try:
            groups = enrollment_service.breakdown_enrollments(
                db, by, limit=limit, **_enrollment_criteria(args)
            )
        except HTTPException as exc:
            return {"error": exc.detail}
        return {"groups": groups}


def _get_enrollment(enrollment_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            enrollment = enrollment_service.get_enrollment(db, int(enrollment_id))
        except HTTPException:
            return {"error": "No enrollment with that id."}
        return enrollment_out(enrollment).model_dump(mode="json")


def _find_courses(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        name = str(args.get("name", ""))
        status = _int(args.get("status"))
        courses = enrollment_service.find_courses(db, name, _limit(args), status=status)
        # No bare `count` key: it read as a total, and was only the page length.
        return {
            "returned": len(courses),
            "total_matching": enrollment_service.count_courses(db, name, status),
            "courses": [
                {
                    "id": c.id,
                    "course_name": c.course_name,
                    "status": "active" if c.status == 1 else "pending",
                    "duration_days": c.duration_days,
                }
                for c in courses
            ],
        }


# ---- assignments ---------------------------------------------------------- #
def _search_assignments(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        criteria = _assignment_criteria(args)
        rows = assignment_service.list_assignments(db, limit=_limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": assignment_service.count_assignments(db, **criteria),
            "assignments": [assignment_summary(a).model_dump(mode="json") for a in rows],
        }


def _count_assignments(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        return {"count": assignment_service.count_assignments(db, **_assignment_criteria(args))}


def _breakdown_assignments(**args: Any) -> dict[str, Any]:
    by = str(args.pop("by", ""))
    limit = _limit(args)
    args.pop("limit", None)
    with PortalSessionLocal() as db:
        try:
            groups = assignment_service.breakdown_assignments(
                db, by, limit=limit, **_assignment_criteria(args)
            )
        except HTTPException as exc:
            return {"error": exc.detail}
        return {"groups": groups}


def _get_assignment(assignment_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            assignment = assignment_service.get_assignment(db, int(assignment_id))
        except HTTPException:
            return {"error": "No assignment with that id."}
        return assignment_out(assignment).model_dump(mode="json")


# ---- student assignments -------------------------------------------------- #
def _search_student_assignments(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        criteria = _student_assignment_criteria(args)
        rows = assignment_service.list_student_assignments(db, limit=_limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": assignment_service.count_student_assignments(db, **criteria),
            "student_assignments": [
                student_assignment_summary(sa).model_dump(mode="json") for sa in rows
            ],
        }


def _count_student_assignments(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        return {
            "count": assignment_service.count_student_assignments(
                db, **_student_assignment_criteria(args)
            )
        }


def _breakdown_student_assignments(**args: Any) -> dict[str, Any]:
    by = str(args.pop("by", ""))
    limit = _limit(args)
    args.pop("limit", None)
    with PortalSessionLocal() as db:
        try:
            groups = assignment_service.breakdown_student_assignments(
                db, by, limit=limit, **_student_assignment_criteria(args)
            )
        except HTTPException as exc:
            return {"error": exc.detail}
        return {"groups": groups}


def _get_enrollment_assignments(enrollment_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        rows = assignment_service.enrollment_assignments(db, int(enrollment_id))
        if not rows:
            # Could be an enrollment with no assignments or no such enrollment;
            # say which, so the model does not guess.
            try:
                enrollment_service.get_enrollment(db, int(enrollment_id))
            except HTTPException:
                return {"error": "No enrollment with that id."}
        return {
            "enrollment_id": int(enrollment_id),
            "count": len(rows),
            "assignments": [
                student_assignment_summary(sa).model_dump(mode="json") for sa in rows
            ],
        }


# ---- results -------------------------------------------------------------- #
def _search_results(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        criteria = _result_criteria(args)
        rows = result_service.list_results(db, limit=_limit(args), **criteria)
        return {
            "returned": len(rows),
            "total_matching": result_service.count_results(db, **criteria),
            "results": [result_summary(r).model_dump(mode="json") for r in rows],
        }


def _count_results(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        return {"count": result_service.count_results(db, **_result_criteria(args))}


def _breakdown_results(**args: Any) -> dict[str, Any]:
    by = str(args.pop("by", ""))
    limit = _limit(args)
    args.pop("limit", None)
    with PortalSessionLocal() as db:
        try:
            groups = result_service.breakdown_results(db, by, limit=limit, **_result_criteria(args))
        except HTTPException as exc:
            return {"error": exc.detail}
        return {"groups": groups}


def _get_result(result_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            result = result_service.get_result(db, int(result_id))
        except HTTPException:
            return {"error": "No result with that id."}
        return result_out(result).model_dump(mode="json")


def _get_student_results(student_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        rows = result_service.student_results(db, int(student_id))
        if not rows:
            try:
                portal_service.get_student(db, int(student_id))
            except HTTPException:
                return {"error": "No student with that id."}
        return {
            "student_id": int(student_id),
            "count": len(rows),
            "results": [result_summary(r).model_dump(mode="json") for r in rows],
        }


# ---- course completion ---------------------------------------------------- #
def _completion_summary(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            return completion_service.summarise(db, **_completion_criteria(args))
        except HTTPException as exc:
            return {"error": exc.detail}


def _search_completions(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        criteria = _completion_criteria(args)
        try:
            rows = completion_service.list_completions(db, limit=_limit(args), **criteria)
            total = completion_service.count_completions(db, **criteria)
        except HTTPException as exc:
            return {"error": exc.detail}
        return {
            "returned": len(rows),
            "total_matching": total,
            "completions": [
                completion_summary(e, mcq).model_dump(mode="json") for e, mcq in rows
            ],
        }


def _count_completions(**args: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            return {"count": completion_service.count_completions(db, **_completion_criteria(args))}
        except HTTPException as exc:
            return {"error": exc.detail}


def _breakdown_completions(**args: Any) -> dict[str, Any]:
    by = str(args.pop("by", ""))
    limit = _limit(args)
    args.pop("limit", None)
    with PortalSessionLocal() as db:
        try:
            groups = completion_service.breakdown_completions(
                db, by, limit=limit, **_completion_criteria(args)
            )
        except HTTPException as exc:
            return {"error": exc.detail}
        return {"groups": groups}


def _get_completion(enrollment_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        try:
            enrollment, mcq_required = completion_service.get_completion(db, int(enrollment_id))
        except HTTPException:
            return {"error": "No enrollment with that id."}
        return {
            **completion_out(enrollment, mcq_required).model_dump(mode="json"),
            "submission_progress": completion_service.submission_progress(db, int(enrollment_id)),
        }


def _get_student_completion(student_id: Any) -> dict[str, Any]:
    with PortalSessionLocal() as db:
        rows = completion_service.student_completions(db, int(student_id))
        if not rows:
            try:
                portal_service.get_student(db, int(student_id))
            except HTTPException:
                return {"error": "No student with that id."}
        return {
            "student_id": int(student_id),
            "count": len(rows),
            "completions": [
                completion_summary(e, mcq).model_dump(mode="json") for e, mcq in rows
            ],
        }


_HANDLERS = {
    "search_students": _search_students,
    "count_students": _count_students,
    "breakdown_students": _breakdown_students,
    "get_student": _get_student,
    "get_student_enrollments": _get_student_enrollments,
    "search_enrollments": _search_enrollments,
    "count_enrollments": _count_enrollments,
    "breakdown_enrollments": _breakdown_enrollments,
    "get_enrollment": _get_enrollment,
    "find_courses": _find_courses,
    "search_assignments": _search_assignments,
    "count_assignments": _count_assignments,
    "breakdown_assignments": _breakdown_assignments,
    "get_assignment": _get_assignment,
    "search_student_assignments": _search_student_assignments,
    "count_student_assignments": _count_student_assignments,
    "breakdown_student_assignments": _breakdown_student_assignments,
    "get_enrollment_assignments": _get_enrollment_assignments,
    "search_results": _search_results,
    "count_results": _count_results,
    "breakdown_results": _breakdown_results,
    "get_result": _get_result,
    "get_student_results": _get_student_results,
    "completion_summary": _completion_summary,
    "search_completions": _search_completions,
    "count_completions": _count_completions,
    "breakdown_completions": _breakdown_completions,
    "get_completion": _get_completion,
    "get_student_completion": _get_student_completion,
}

# The feedback, catalogue, staff, student-extras and reference tools live in
# their own module; they join the same declarations and handlers so `dispatch()`
# treats them exactly like the ones above.
TOOL_DECLARATIONS.function_declarations.extend(ai_tools_extra.DECLARATIONS)
_HANDLERS.update(ai_tools_extra.HANDLERS)

# Parameter names the model is actually allowed to send, taken from the same
# declarations it was given, so the two cannot drift apart.
_DECLARED_PARAMS = {
    f.name: set((f.parameters.properties or {}).keys())
    for f in TOOL_DECLARATIONS.function_declarations
}


def dispatch(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    """Run one model-requested tool call.

    Never raises: a failure is returned as data so the conversation can carry
    on and the model can explain itself.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        logger.warning("model requested unknown tool %r", name)
        return {"error": f"Unknown tool {name!r}."}

    # An invented parameter name must be an error, never ignored: a silently
    # dropped filter would turn "how many students in Delhi" into the count of
    # every student, and the model would report it as the answer.
    unknown = set(args or {}) - _DECLARED_PARAMS[name]
    if unknown:
        allowed = ", ".join(sorted(_DECLARED_PARAMS[name]))
        return {
            "error": f"Unknown parameter(s) for {name}: {', '.join(sorted(unknown))}. "
            f"Allowed: {allowed}."
        }

    try:
        result = handler(**dict(args or {}))
    except (TypeError, ValueError) as exc:
        return {"error": f"Bad arguments for {name}: {exc}"}
    except OperationalError as exc:
        if getattr(exc.orig, "args", [None])[0] != _STATEMENT_TIMEOUT_CODE:
            logger.exception("tool %r failed", name)
            return {"error": f"Tool {name} failed."}
        # Expected on the biggest tables, not a fault: one line, no traceback.
        # And say plainly not to retry -- with "Tool X failed" the model tried
        # the same 15s query up to six times, two minutes for one question.
        logger.warning("tool %r stopped at the portal statement cap", name)
        return {
            "error": (
                f"This query ran past the portal's {settings.PORTAL_STATEMENT_TIMEOUT_SECONDS:g}s "
                "limit and was stopped. Do not retry it unchanged. Narrow it with a filter "
                "(a course, student, enrollment or date range), answer from a different tool "
                "that already covers the question, or tell the user it is too large to answer."
            )
        }
    except Exception:
        logger.exception("tool %r failed", name)
        return {"error": f"Tool {name} failed."}

    # Logged without the result: rows carry student data.
    logger.info("tool %s called with %s", name, sorted((args or {}).keys()))
    return result
