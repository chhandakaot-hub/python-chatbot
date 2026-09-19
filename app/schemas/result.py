"""Shapes for portal results -- submissions and their evaluations.

No feedback text appears here, and none can: the columns holding it are not
mapped on the model (see `app/models/portal/result.py`), and
`tests/test_results.py` fails if a field named after one is ever added.

`score` is the reviewer's edited score when a reviewer set one, the AI's score
otherwise. Both are also reported separately, because "the AI gave 62, the
reviewer corrected it to 71" is a different fact from "the score is 71".
"""

from datetime import datetime

from pydantic import BaseModel

from app.models.portal.codes import RESULT_STATUS_LABELS, ResultStatus, label
from app.models.portal.result import Result


class ResultSummary(BaseModel):
    """One line per submission: used for lists and for AI tool results."""

    id: int
    student_id: int
    student_assignment_id: int
    assignment_code: str | None
    course_name: str | None
    status: int
    status_label: str
    evaluated: bool
    is_latest: bool
    score: float | None
    submitted_date: datetime | None
    evaluation_date: datetime | None


class ResultOut(ResultSummary):
    """Full detail for a single submission."""

    enrollment_id: int | None
    ai_score: float | None
    reviewer_edited_score: float | None
    ai_evaluation_status: str | None
    ai_evaluated_at: datetime | None
    reviewed_at: datetime | None
    review_done: bool
    plagiarism_result: int | None
    waive_marks: int | None
    featured: bool
    email_sent: bool
    evaluation_due_date: datetime | None
    created_at: datetime | None
    updated_at: datetime | None


def _score(value) -> float | None:
    return None if value is None else float(value)


def result_summary(r: Result) -> ResultSummary:
    """Requires `student_assignment` (with its assignment and enrollment) to be
    eager-loaded -- the relationship is lazy='raise'."""
    sa = r.student_assignment
    assignment = sa.course_assignment if sa else None
    enrollment = sa.enrollment if sa else None
    return ResultSummary(
        id=r.id,
        student_id=r.student_id,
        # Named for what the column actually points at. The portal calls it
        # `assignment_id`, but it is a student_assignments id.
        student_assignment_id=r.assignment_id,
        assignment_code=assignment.assignment_code if assignment else None,
        course_name=enrollment.course.course_name if enrollment and enrollment.course else None,
        status=r.status,
        status_label=label(RESULT_STATUS_LABELS, r.status),
        evaluated=r.status == ResultStatus.EVALUATED,
        is_latest=bool(r.latest),
        score=_score(r.reviewer_edited_score if r.reviewer_edited_score is not None else r.ai_score),
        submitted_date=r.submitted_date,
        evaluation_date=r.evaluation_date,
    )


def result_out(r: Result) -> ResultOut:
    """Same eager-loading requirements as `result_summary`."""
    sa = r.student_assignment
    return ResultOut(
        **result_summary(r).model_dump(),
        enrollment_id=sa.enrollment_id if sa else None,
        ai_score=_score(r.ai_score),
        reviewer_edited_score=_score(r.reviewer_edited_score),
        ai_evaluation_status=r.ai_evaluation_status,
        ai_evaluated_at=r.ai_evaluated_at,
        reviewed_at=r.reviewed_at,
        review_done=bool(r.is_review_done),
        plagiarism_result=r.plagiarism_result,
        waive_marks=r.waive_marks,
        featured=bool(r.feature_assignment),
        email_sent=bool(r.is_email_sent),
        evaluation_due_date=r.evaluation_due_date,
        created_at=r.created_at,
        updated_at=r.updated_at,
    )
