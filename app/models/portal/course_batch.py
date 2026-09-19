"""The portal's `course_batches` table.

`batch_date` is the batch's human label (unique), not a date column.
Deliberately NOT mapped: the staff ids and the Edmingle sync bookkeeping.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class CourseBatch(PortalBase):
    __tablename__ = "course_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_date: Mapped[str] = mapped_column(String(255))
    start_date: Mapped[date | None]
    status: Mapped[int] = mapped_column(Integer)

    deleted_at: Mapped[datetime | None]

    def __repr__(self) -> str:
        return f"<CourseBatch id={self.id} label={self.batch_date!r}>"
