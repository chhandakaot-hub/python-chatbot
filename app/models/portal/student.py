"""The portal's `students` table.

Only columns on the allow-list below are mapped. This is the privacy boundary
for this table: an unmapped column is never named in a SELECT, so it cannot
reach the API, a prompt, or a log even by accident.

Deliberately NOT mapped -- credentials and secrets:
    password, forum_pass, forum_access_token, edmingle_api_key,
    remember_token, otp, verification_otp, tmp_verification_token

Deliberately NOT mapped -- personal details the bot has no need for:
    date_of_birth, father_name, address, pin_code, phone, gender,
    id_image, image, linked_in_link
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class Student(PortalBase):
    __tablename__ = "students"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    reg_code: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)

    city: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(255))
    what_are_you_doing_currently: Mapped[str | None] = mapped_column(String(255))

    enrollment_form_filled_at: Mapped[datetime | None]
    email_verified_at: Mapped[datetime | None]
    last_login: Mapped[datetime | None]
    created_at: Mapped[datetime | None]

    def __repr__(self) -> str:
        # No name or email in the repr: these end up in logs and tracebacks.
        return f"<Student id={self.id} reg_code={self.reg_code!r}>"
