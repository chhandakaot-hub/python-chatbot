"""The portal's `student_assignments` table -- one assignment as handed to one
enrollment. ~4M rows, the largest table the bot reads.

This is the join between an enrollment and an assignment definition, carrying
the deadline, how many times the student has submitted, and where the
submission has got to. The submissions themselves are `results` rows.

Mapped: the links (enrollment, assignment), deadline, submit counter, status,
size and whether it is mandatory.

Deliberately NOT mapped:

* per-student overrides of the content links --
  `assignment_instruction_link`, `assignment_sample_feedback_link`, left out
  for the same reason as on `assignments`;
* AI configuration -- `ai_model_id`, `is_ai_enabled`;
* staff ids -- `created_by`, `updated_by`.

Soft deletes are real here (`deleted_at`), so every query in
`assignment_service` excludes them.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.portal_database import PortalBase


class StudentAssignment(PortalBase):
    __tablename__ = "student_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    enrollment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("enrollments.id"))
    assignment_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("assignments.id"))

    submission_last_date: Mapped[date | None]
    submit_counter: Mapped[int] = mapped_column(Integer)
    status: Mapped[int] = mapped_column(Integer)
    number_of_exercises: Mapped[int] = mapped_column(Integer)
    mandatory: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    course_assignment: Mapped["Assignment"] = relationship(lazy="raise")  # noqa: F821
    enrollment: Mapped["Enrollment"] = relationship(lazy="raise")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<StudentAssignment id={self.id} enrollment_id={self.enrollment_id} "
            f"status={self.status}>"
        )
