"""The portal's `results` table -- one submission of one student assignment,
with its evaluation.

**`results.assignment_id` points at `student_assignments.id`, not at
`assignments.id`.** The portal's own model says so --
`Result::belongsTo(StudentAssignment::class, 'assignment_id')` in
Modules/Result/Entities/Result.php -- and the data agrees: all 123,355 rows
join to `student_assignments`, while only 106,712 happen to collide with an
`assignments` id. Joining this column to `assignments` returns rows, silently
about the wrong assignment, which is why `result_service` never does and
`tests/test_results.py` pins it.

Mapped: the links, lifecycle state, the numeric scores, and the dates that
answer "is this evaluated, and was it on time".

Deliberately NOT mapped -- feedback and other free text:
    ai_feedback, reviewer_edited_feedback, feedback_to_student,
    resubmission_feedback, reason, feedback_edit_reason
Written about a named student, and any text a person or a model typed is a
prompt-injection vector once it reaches the model.

Deliberately NOT mapped -- files and links:
    submitted_file, feedback_file, feedback_file_original_name, feedback_link,
    ai_feedback_pdf_url, plagiarism_result_file

Deliberately NOT mapped -- bulky internals and provider bookkeeping:
    unicheck_details, unicheck_file_details, unicheck_check_details,
    auto_assignment_response, ai_evaluation_uuid, ai_model_id,
    ai_instruction_source, ai_feedback_sample_source

Deliberately NOT mapped -- staff ids:
    evaluator_id, reviewed_by, updated_by
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.portal_database import PortalBase


class Result(PortalBase):
    __tablename__ = "results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("students.id"))
    # Named `assignment_id` by the portal, but it is a student_assignments id.
    # The relationship below is the only sanctioned way to follow it.
    assignment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("student_assignments.id")
    )

    status: Mapped[int] = mapped_column(Integer)
    plagiarism_result: Mapped[int | None] = mapped_column(Integer)

    submitted_date: Mapped[datetime | None]
    evaluation_date: Mapped[datetime | None]
    evaluation_due_date: Mapped[datetime | None]

    ai_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    ai_evaluation_status: Mapped[str | None] = mapped_column(String(255))
    ai_evaluated_at: Mapped[datetime | None]
    reviewer_edited_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    reviewed_at: Mapped[datetime | None]

    is_email_sent: Mapped[int] = mapped_column(Integer)
    is_review_done: Mapped[int] = mapped_column(Integer)
    is_evaluated: Mapped[int | None] = mapped_column(Integer)
    waive_marks: Mapped[int | None] = mapped_column(Integer)
    feature_assignment: Mapped[int] = mapped_column(Integer)
    # 1 on the current submission, 0 on superseded resubmissions.
    latest: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    student_assignment: Mapped["StudentAssignment"] = relationship(lazy="raise")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Result id={self.id} student_assignment_id={self.assignment_id} status={self.status}>"
