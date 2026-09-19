"""The portal's `topics` table -- the syllabus topic an assignment belongs to.

Only the id and title are mapped: the title is what makes an assignment
recognisable to a person ("Contract Drafting: Indemnity clauses") where
`assignment_code` alone does not. The staff ids are left out, as everywhere
else.
"""

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class Topic(PortalBase):
    __tablename__ = "topics"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str | None] = mapped_column(String(255))

    def __repr__(self) -> str:
        return f"<Topic id={self.id} title={self.title!r}>"
