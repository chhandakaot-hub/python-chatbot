"""Code values stored in the portal's integer columns.

Confirmed against the portal's Laravel source, not inferred from the data:

    Lawsikho-Assignment-Portal-API/Modules/Enrollment/Entities/Enrollment.php
    Lawsikho-Assignment-Portal-API/Modules/Course/Entities/Course.php
    Lawsikho-Assignment-Portal-API/Modules/Assignment/Entities/Assignment.php
    Lawsikho-Assignment-Portal-API/Modules/StudentAssignment/Entities/StudentAssignment.php
    Lawsikho-Assignment-Portal-API/Modules/Result/Entities/Result.php

If the portal adds a code, add it here; `label()` reports anything unknown as
"unknown (n)" rather than guessing.
"""

from enum import IntEnum


class EnrollmentStatus(IntEnum):
    PENDING = 0
    ACTIVE = 1
    PAUSED = 2
    RESUME_REQUESTED = 3
    PAUSE_REQUESTED = 4  # a refund-eligible pause has been requested


class EnrollmentType(IntEnum):
    NORMAL = 1
    PACKAGE = 2
    BOOTCAMP = 3
    PACKAGE_BATCH = 4


class CourseType(IntEnum):
    SIMPLE = 1
    BOOTCAMP = 2


class CourseStatus(IntEnum):
    PENDING = 0
    ACTIVE = 1


class AssignmentStatus(IntEnum):
    """Modules/Assignment/Entities/Assignment.php"""

    DEACTIVE = 0
    ACTIVE = 1


class AssignmentType(IntEnum):
    """Subjective exercises vs a written assignment. Note 0 is a real type
    here, not "unset" -- roughly half of all assignments are subjective."""

    SUBJECTIVE = 0
    WRITTEN = 1


class Plagiarism(IntEnum):
    """`assignments.plagiarism` -- whether the assignment is plagiarism-checked."""

    NO = 0
    YES = 1


class StudentAssignmentStatus(IntEnum):
    """Modules/StudentAssignment/Entities/StudentAssignment.php

    The lifecycle of one assignment handed to one enrollment. 0 and 1 are
    activation states (the assignment is switched on for the student but
    untouched); 3, 4 and 5 are the states a person would call progress.
    """

    DEACTIVE = 0
    ACTIVE = 1
    PENDING = 2
    SUBMITTED = 3
    RESUBMITTED = 4
    EVALUATED = 5


class ResultStatus(IntEnum):
    """Modules/Result/Entities/Result.php

    There is deliberately no 4: the portal's constants skip it.
    """

    DEACTIVE = 0
    ACTIVE = 1
    PENDING = 2
    RESUBMIT = 3
    EVALUATED = 5


class StudentStatus(IntEnum):
    """Modules/Student/Entities/Student.php"""

    PENDING = 0
    ACTIVE = 1
    DISABLED = 2


STUDENT_STATUS_LABELS = {
    StudentStatus.PENDING: "pending",
    StudentStatus.ACTIVE: "active",
    StudentStatus.DISABLED: "disabled",
}


ENROLLMENT_STATUS_LABELS = {
    EnrollmentStatus.PENDING: "pending",
    EnrollmentStatus.ACTIVE: "active",
    EnrollmentStatus.PAUSED: "paused",
    EnrollmentStatus.RESUME_REQUESTED: "resume requested",
    EnrollmentStatus.PAUSE_REQUESTED: "pause requested (refund eligible)",
}

ENROLLMENT_TYPE_LABELS = {
    EnrollmentType.NORMAL: "normal",
    EnrollmentType.PACKAGE: "package",
    EnrollmentType.BOOTCAMP: "bootcamp",
    EnrollmentType.PACKAGE_BATCH: "package batch",
}

ASSIGNMENT_STATUS_LABELS = {
    AssignmentStatus.DEACTIVE: "deactivated",
    AssignmentStatus.ACTIVE: "active",
}

ASSIGNMENT_TYPE_LABELS = {
    AssignmentType.SUBJECTIVE: "subjective",
    AssignmentType.WRITTEN: "written",
}

STUDENT_ASSIGNMENT_STATUS_LABELS = {
    StudentAssignmentStatus.DEACTIVE: "deactivated",
    StudentAssignmentStatus.ACTIVE: "active (not submitted)",
    StudentAssignmentStatus.PENDING: "pending",
    StudentAssignmentStatus.SUBMITTED: "submitted",
    StudentAssignmentStatus.RESUBMITTED: "resubmitted",
    StudentAssignmentStatus.EVALUATED: "evaluated",
}

RESULT_STATUS_LABELS = {
    ResultStatus.DEACTIVE: "deactivated",
    ResultStatus.ACTIVE: "active (awaiting evaluation)",
    ResultStatus.PENDING: "pending",
    ResultStatus.RESUBMIT: "resubmission requested",
    ResultStatus.EVALUATED: "evaluated",
}

# Course completion is not a single stored flag. `enrollments.completed` means
# "passed the marks criteria"; when the course also requires an LMS MCQ, the
# portal will not call it complete until `mcq_completed` is set too. See
# Modules/CourseCompletionMaster/Http/Resources/CourseCompletionMasterResource.php
# (getCompleted), which this mirrors.
COMPLETION_COMPLETED = "completed"
COMPLETION_AWAITING_MCQ = "criteria met, awaiting LMS MCQ confirmation"
COMPLETION_NOT_COMPLETED = "not completed"


COURSE_TYPE_LABELS = {
    CourseType.SIMPLE: "simple",
    CourseType.BOOTCAMP: "bootcamp",
}


def label(labels: dict[int, str], code: object) -> str:
    """Human label for a stored code. `enrollments.type` is a varchar, so
    string codes like "3" are accepted."""
    try:
        return labels.get(int(code), f"unknown ({code})")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return f"unknown ({code})"
