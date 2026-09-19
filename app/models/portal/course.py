"""The portal's `courses` table.

Mapped: identity, name, and the handful of flags useful for answering staff
questions. Deliberately NOT mapped: instruction/feedback links, image path,
AI model settings, and the evaluator / coach / placement staff ids.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class Course(PortalBase):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)            # 1 active, 0 inactive
    duration_days: Mapped[int] = mapped_column(Integer)
    course_type: Mapped[int] = mapped_column(Integer)
    course_category_id: Mapped[int | None] = mapped_column(BigInteger)
    is_job_eligible: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]

    def __repr__(self) -> str:
        return f"<Course id={self.id} name={self.course_name!r}>"
