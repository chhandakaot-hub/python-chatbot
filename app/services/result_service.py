"""Read-only queries over portal results -- submissions and their evaluations.

Same rules as the other portal services: allow-listed columns, capped lists,
bound parameters, one shared filter builder.

The join to follow: `results.assignment_id` is a **student_assignments** id,
never an `assignments` id. Everything here goes through
`Result.student_assignment`, so the mistake cannot be made by accident; see the
module docstring on `app/models/portal/result.py` for the evidence.
"""

from datetime import date

from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session, contains_eager

from app.models.portal.assignment import Assignment
from app.models.portal.codes import RESULT_STATUS_LABELS, ResultStatus, label
from app.models.portal.course import Course
from app.models.portal.enrollment import Enrollment
from app.models.portal.result import Result
from app.models.portal.student_assignment import StudentAssignment
from app.services.portal_service import MAX_RESULTS

# One student's submission history across every course.
MAX_PER_STUDENT = 50


def _live() -> ColumnElement[bool]:
    """Soft-deleted rows never count. None exist today; that can change."""
    return Result.deleted_at.is_(None)


def build_filters(
    student_id: int | None = None,
    student_assignment_id: int | None = None,
    enrollment_id: int | None = None,
    course_id: int | None = None,
    course_name: str | None = None,
    status: int | None = None,
    evaluated: bool | None = None,
    latest_only: bool | None = None,
    awaiting_evaluation: bool | None = None,
    overdue_evaluation: bool | None = None,
    plagiarism_flagged: bool | None = None,
    ai_evaluated: bool | None = None,
    min_score: float | None = None,
    max_score: float | None = None,
    submitted_after: date | None = None,
    submitted_before: date | None = None,
    evaluated_after: date | None = None,
) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller.

    `enrollment_id`, `course_id` and `course_name` reach through the student
    assignment to its enrollment, so statements built from these join
    StudentAssignment, Enrollment and Course.
    """
    conditions: list[ColumnElement[bool]] = [_live()]

    if student_id is not None:
        conditions.append(Result.student_id == student_id)
    # Named for what it actually is, not for the portal's column name.
    if student_assignment_id is not None:
        conditions.append(Result.assignment_id == student_assignment_id)
    if enrollment_id is not None:
        conditions.append(StudentAssignment.enrollment_id == enrollment_id)
    if course_id is not None:
        conditions.append(Enrollment.course_id == course_id)
    if course_name and course_name.strip():
        conditions.append(Course.course_name.like(f"%{course_name.strip()}%"))

    if status is not None:
        conditions.append(Result.status == status)

    if evaluated is True:
        conditions.append(Result.status == ResultStatus.EVALUATED)
    elif evaluated is False:
        conditions.append(Result.status != ResultStatus.EVALUATED)

    # A resubmission supersedes the row before it. Counting every row answers
    # "how many submissions"; counting latest only answers "how many students".
    if latest_only is True:
        conditions.append(Result.latest == 1)
    elif latest_only is False:
        conditions.append(func.coalesce(Result.latest, 0) == 0)

    if awaiting_evaluation is True:
        conditions.append(Result.status == ResultStatus.ACTIVE)
        conditions.append(Result.evaluation_date.is_(None))
    elif awaiting_evaluation is False:
        conditions.append(
            (Result.status != ResultStatus.ACTIVE) | Result.evaluation_date.is_not(None)
        )

    # Past its due date and still not evaluated. A due date in the past on an
    # evaluated submission is history, not a backlog item.
    if overdue_evaluation is True:
        conditions.append(Result.evaluation_due_date < func.now())
        conditions.append(Result.evaluation_date.is_(None))
    elif overdue_evaluation is False:
        conditions.append(
            (Result.evaluation_due_date >= func.now())
            | Result.evaluation_due_date.is_(None)
            | Result.evaluation_date.is_not(None)
        )

    if plagiarism_flagged is True:
        conditions.append(Result.plagiarism_result > 0)
    elif plagiarism_flagged is False:
        conditions.append(func.coalesce(Result.plagiarism_result, 0) == 0)

    if ai_evaluated is True:
        conditions.append(Result.ai_evaluated_at.is_not(None))
    elif ai_evaluated is False:
        conditions.append(Result.ai_evaluated_at.is_(None))

    # The score that counts is the reviewer's when there is one, the AI's
    # otherwise -- the same precedence the schema reports as `score`.
    effective_score = func.coalesce(Result.reviewer_edited_score, Result.ai_score)
    if min_score is not None:
        conditions.append(effective_score >= min_score)
    if max_score is not None:
        conditions.append(effective_score <= max_score)

    if submitted_after:
        conditions.append(Result.submitted_date >= submitted_after)
    if submitted_before:
        conditions.append(Result.submitted_date < submitted_before)
    if evaluated_after:
        conditions.append(Result.evaluation_date >= evaluated_after)

    return conditions


# Criteria that can only be answered by reaching past `results` into the
# student assignment and its enrollment. Anything else needs no join at all,
# and joining 123k results to a 4M-row table to group by results.status costs
# twenty seconds for nothing.
_NEEDS_ENROLLMENT = ("enrollment_id", "course_id", "course_name")


def _joins_enrollment(criteria: dict) -> bool:
    return any(criteria.get(key) not in (None, "") for key in _NEEDS_ENROLLMENT)


def _scoped(statement: Select, *, enrollment: bool, assignment: bool = False) -> Select:
    """Add only the joins this statement actually needs."""
    if enrollment or assignment:
        statement = statement.join(Result.student_assignment)
    if enrollment:
        statement = statement.join(StudentAssignment.enrollment).join(Enrollment.course)
    if assignment:
        statement = statement.join(StudentAssignment.course_assignment)
    return statement


def _select(**criteria) -> Select:
    """A row-returning select. Always eager-loads, because the schema reports
    the assignment code and course name for every row."""
    return (
        select(Result)
        .join(Result.student_assignment)
        .join(StudentAssignment.enrollment)
        .join(Enrollment.course)
        .join(StudentAssignment.course_assignment)
        .options(
            contains_eager(Result.student_assignment).contains_eager(
                StudentAssignment.course_assignment
            ),
            contains_eager(Result.student_assignment)
            .contains_eager(StudentAssignment.enrollment)
            .contains_eager(Enrollment.course),
        )
        .where(*build_filters(**criteria))
    )


def get_result(db: Session, result_id: int) -> Result:
    result = db.scalar(_select().where(Result.id == result_id))
    if result is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Result not found")
    return result


def list_results(db: Session, limit: int = 10, **criteria) -> list[Result]:
    """Most recent submissions matching every criterion, capped at MAX_RESULTS."""
    statement = (
        _select(**criteria)
        .order_by(Result.submitted_date.desc(), Result.id.desc())
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    return list(db.scalars(statement).unique())


def student_results(db: Session, student_id: int) -> list[Result]:
    """One student's current submissions, newest first.

    Latest only: a student who resubmitted four times has four rows for one
    assignment, and a list of those reads as four separate pieces of work.
    """
    statement = (
        _select(student_id=student_id, latest_only=True)
        .order_by(Result.submitted_date.desc(), Result.id.desc())
        .limit(MAX_PER_STUDENT)
    )
    return list(db.scalars(statement).unique())


def count_results(db: Session, **criteria) -> int:
    """True number of matches -- not the capped page size."""
    statement = _scoped(
        select(func.count(Result.id)), enrollment=_joins_enrollment(criteria)
    ).where(*build_filters(**criteria))
    return db.scalar(statement) or 0


_LABELLED = {"status": RESULT_STATUS_LABELS}


def breakdown_results(db: Session, by: str, limit: int = 10, **criteria) -> list[dict]:
    """Result counts grouped by one dimension.

    `by` is looked up in an allow-list, never used as a column name directly,
    and the check runs before any database access.
    """
    submitted_month = func.date_format(Result.submitted_date, "%Y-%m")
    evaluated_month = func.date_format(Result.evaluation_date, "%Y-%m")
    dimensions = {
        "status": (Result.status, False),
        "course": (Course.course_name, False),
        "plagiarism": (func.coalesce(Result.plagiarism_result, 0), False),
        "review_done": (Result.is_review_done, False),
        "ai_evaluation_status": (Result.ai_evaluation_status, False),
        "assignment_type": (Assignment.assignment_type, True),
        "submitted_month": (submitted_month, False),
        "evaluated_month": (evaluated_month, False),
    }
    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )

    field, needs_assignment = dimensions[by]
    statement = _scoped(
        select(field.label("value"), func.count(Result.id).label("count")),
        # "course" is the only dimension that needs the enrollment; the rest
        # are columns on `results` itself.
        enrollment=(by == "course") or _joins_enrollment(criteria),
        assignment=needs_assignment,
    )

    order = field.desc() if by.endswith("month") else func.count(Result.id).desc()
    statement = (
        statement.where(*build_filters(**criteria))
        .group_by(field)
        .order_by(order)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )

    labels = _LABELLED.get(by)
    return [
        {"value": value, "count": count, **({"label": label(labels, value)} if labels else {})}
        for value, count in db.execute(statement)
    ]
