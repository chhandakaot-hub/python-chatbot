"""The portal's `assignments` table -- the assignment *definitions* a course
sets, not anything a student did. ~2.7M rows.

One row is "assignment N of course C, on topic T". What a particular student
did with it lives in `student_assignments` and `results`.

Mapped: the links (course, topic), the code, what kind of assignment it is,
its size, and whether it is switched on.

Deliberately NOT mapped:

* content links -- `assignment_instruction_link`,
  `assignment_sample_feedback_link`, `assignment_download_file`. These are the
  assignment's own material; the bot answers questions *about* assignments, and
  a link served to the model is neither useful nor safe to hand on;
* `allowed_file_types` -- a JSON blob of upload configuration;
* AI configuration -- `ai_model_id`, `is_ai_enabled`, matching the course model,
  which leaves the same settings out;
* staff ids -- `created_by`, `updated_by`;
* `package_id` -- a commerce reference, as on Enrollment.
"""

from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.portal_database import PortalBase


class Assignment(PortalBase):
    __tablename__ = "assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    # ForeignKey here only tells SQLAlchemy how to join; nothing is created.
    course_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("courses.id"))
    topic_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("topics.id"))

    assignment_code: Mapped[str] = mapped_column(String(255))
    assignment_type: Mapped[int] = mapped_column(Integer)   # 0 subjective, 1 written
    number_of_exercises: Mapped[int] = mapped_column(Integer)
    word_count: Mapped[int] = mapped_column(Integer)
    plagiarism: Mapped[int] = mapped_column(Integer)        # 0 no, 1 checked
    status: Mapped[int] = mapped_column(Integer)            # 0 deactivated, 1 active
    ref_assignment_no: Mapped[int | None] = mapped_column(Integer)
    is_bootcamp_written: Mapped[int] = mapped_column(Integer)
    auto_assignment: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]

    # lazy="raise": every load must be explicit in the service, so a stray
    # attribute access can never fire an unplanned query per row.
    course: Mapped["Course"] = relationship(lazy="raise")  # noqa: F821
    topic: Mapped["Topic | None"] = relationship(lazy="raise")

    def __repr__(self) -> str:
        return f"<Assignment id={self.id} code={self.assignment_code!r} course_id={self.course_id}>"
