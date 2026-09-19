"""Shapes for portal assignments and student assignments.

Built with functions rather than `from_attributes`, for the same reasons as
`schemas/enrollment.py`: course and topic names live on related rows, the
portal's 0/1 integer flags read better as booleans, and a stored code like
status 4 means nothing to a person -- or to the model -- without its label.
"""

from datetime import date, datetime

from pydantic import BaseModel

from app.models.portal.assignment import Assignment
from app.models.portal.codes import (
    ASSIGNMENT_STATUS_LABELS,
    ASSIGNMENT_TYPE_LABELS,
    STUDENT_ASSIGNMENT_STATUS_LABELS,
    label,
)
from app.models.portal.student_assignment import StudentAssignment


def _flag(value: int | None) -> bool | None:
    return None if value is None else bool(value)


# --------------------------------------------------------------------------- #
# assignments
# --------------------------------------------------------------------------- #
class AssignmentSummary(BaseModel):
    """One line per assignment: used for lists and for AI tool results."""

    id: int
    course_id: int
    course_name: str | None
    topic: str | None
    assignment_code: str
    assignment_type: int
    type_label: str
    number_of_exercises: int
    status: int
    status_label: str


class AssignmentOut(AssignmentSummary):
    """Full detail for a single assignment."""

    topic_id: int
    word_count: int
    plagiarism_checked: bool
    ref_assignment_no: int | None
    is_bootcamp_written: bool
    auto_assignment: bool
    created_at: datetime | None
    updated_at: datetime | None


def assignment_summary(a: Assignment) -> AssignmentSummary:
    """Requires `course` and `topic` to be eager-loaded (they are lazy='raise')."""
    return AssignmentSummary(
        id=a.id,
        course_id=a.course_id,
        course_name=a.course.course_name if a.course else None,
        topic=a.topic.title if a.topic else None,
        assignment_code=a.assignment_code,
        assignment_type=a.assignment_type,
        type_label=label(ASSIGNMENT_TYPE_LABELS, a.assignment_type),
        number_of_exercises=a.number_of_exercises,
        status=a.status,
        status_label=label(ASSIGNMENT_STATUS_LABELS, a.status),
    )


def assignment_out(a: Assignment) -> AssignmentOut:
    """Requires `course` and `topic` to be eager-loaded (they are lazy='raise')."""
    return AssignmentOut(
        **assignment_summary(a).model_dump(),
        topic_id=a.topic_id,
        word_count=a.word_count,
        plagiarism_checked=bool(a.plagiarism),
        ref_assignment_no=a.ref_assignment_no,
        is_bootcamp_written=bool(a.is_bootcamp_written),
        auto_assignment=bool(a.auto_assignment),
        created_at=a.created_at,
        updated_at=a.updated_at,
    )


# --------------------------------------------------------------------------- #
# student_assignments
# --------------------------------------------------------------------------- #
class StudentAssignmentSummary(BaseModel):
    """One assignment as handed to one enrollment."""

    id: int
    enrollment_id: int
    student_id: int | None
    assignment_id: int
    assignment_code: str | None
    topic: str | None
    course_name: str | None
    status: int
    status_label: str
    submitted: bool
    submission_last_date: date | None
    submit_counter: int
    mandatory: bool


class StudentAssignmentOut(StudentAssignmentSummary):
    """Full detail for a single student assignment."""

    course_id: int | None
    assignment_type: int | None
    type_label: str | None
    number_of_exercises: int
    created_at: datetime | None
    updated_at: datetime | None


def student_assignment_summary(sa: StudentAssignment) -> StudentAssignmentSummary:
    """Requires `course_assignment` (with its topic) and `enrollment` (with its
    course) to be eager-loaded -- every relationship here is lazy='raise'."""
    from app.services.assignment_service import SUBMITTED_STATUSES

    assignment = sa.course_assignment
    enrollment = sa.enrollment
    return StudentAssignmentSummary(
        id=sa.id,
        enrollment_id=sa.enrollment_id,
        student_id=enrollment.student_id if enrollment else None,
        assignment_id=sa.assignment_id,
        assignment_code=assignment.assignment_code if assignment else None,
        topic=assignment.topic.title if assignment and assignment.topic else None,
        course_name=enrollment.course.course_name if enrollment and enrollment.course else None,
        status=sa.status,
        status_label=label(STUDENT_ASSIGNMENT_STATUS_LABELS, sa.status),
        submitted=sa.status in SUBMITTED_STATUSES,
        submission_last_date=sa.submission_last_date,
        submit_counter=sa.submit_counter,
        mandatory=bool(sa.mandatory),
    )


def student_assignment_out(sa: StudentAssignment) -> StudentAssignmentOut:
    """Same eager-loading requirements as `student_assignment_summary`."""
    assignment = sa.course_assignment
    enrollment = sa.enrollment
    return StudentAssignmentOut(
        **student_assignment_summary(sa).model_dump(),
        course_id=enrollment.course_id if enrollment else None,
        assignment_type=assignment.assignment_type if assignment else None,
        type_label=(
            label(ASSIGNMENT_TYPE_LABELS, assignment.assignment_type) if assignment else None
        ),
        number_of_exercises=sa.number_of_exercises,
        created_at=sa.created_at,
        updated_at=sa.updated_at,
    )
