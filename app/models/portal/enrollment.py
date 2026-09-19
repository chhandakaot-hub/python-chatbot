"""The portal's `enrollments` table -- one row per student per course.

Mapped: the links (student, course, batch), lifecycle state, progress,
completion, certification and expiry.

Deliberately NOT mapped:

* free-text notes -- `comment`, `pause_reason`, `paused_reason`,
  `deactivation_reason`. They can hold personal circumstances, and any text a
  person typed is a prompt-injection vector once it reaches the model;
* commerce references -- `ls_order_id`, `package_id`, `reference_package`;
* bulky internals -- `passing_criteria`, `dashboard_journey_steps`,
  `certificate_file`;
* staff ids -- `created_by`, `updated_by`, `certified_by`, `batch_assigned_by`;
* sync and migration bookkeeping.

The table's primary key is (id, created_at), but `id` is unique on its own,
so it alone is used as the mapper identity.
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.portal_database import PortalBase


class Enrollment(PortalBase):
    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_code: Mapped[str | None] = mapped_column(String(255))

    # ForeignKey here only tells SQLAlchemy how to join; nothing is created.
    student_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("students.id"))
    course_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("courses.id"))
    batch_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("course_batches.id"))

    status: Mapped[int] = mapped_column(Integer)
    type: Mapped[str | None] = mapped_column(String(255))
    course_activated: Mapped[int | None] = mapped_column(Integer)
    bootcamp_name: Mapped[str | None] = mapped_column(String(255))

    pause_status: Mapped[str | None] = mapped_column(String(64))
    paused_at: Mapped[datetime | None]

    current_percent: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    # The two halves behind current_percent, needed by the completion view to
    # say *which* half a student is short on.
    subjective_passing_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    written_passing_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    completed: Mapped[int | None] = mapped_column(Integer)
    completed_at: Mapped[datetime | None]
    mcq_completed: Mapped[int | None] = mapped_column(Integer)
    mcq_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    is_certified: Mapped[int] = mapped_column(Integer)
    certified_datetime: Mapped[datetime | None]

    enrollment_expire_at: Mapped[datetime | None]
    course_expiry_date: Mapped[datetime | None]
    created_at: Mapped[datetime]
    deleted_at: Mapped[datetime | None]

    # lazy="raise": every load must be explicit in the service, so a stray
    # attribute access can never fire an unplanned query per row.
    student: Mapped["Student"] = relationship(lazy="raise")  # noqa: F821
    course: Mapped["Course"] = relationship(lazy="raise")  # noqa: F821
    batch: Mapped["CourseBatch | None"] = relationship(lazy="raise")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Enrollment id={self.id} course_id={self.course_id} status={self.status}>"
