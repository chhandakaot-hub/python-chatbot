"""Per-student side tables: pause history, availability, "how did you hear
about us", and a count-only view of internal notes.

Deliberately NOT mapped:

* `students_internal_notes.notes` -- the note body. Staff write these about
  individual students, and they are exactly the free text this bot must never
  read. The model carries only the columns needed to say how many exist and
  when the latest was written.
* `enrollment_pause_log_new.paused_reason` (free text), `support_ticket_id`
  and the student/admin ids.
* `know_about_lawsikho_student_answer.is_other` -- the typed "other" answer.
* `enrollment_question_answers`, `student_original_registration_details` (raw
  registration JSON) and `student_other_details` (third-party ids).
"""

from datetime import datetime, time

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class EnrollmentPauseLog(PortalBase):
    """`status` is one of paused / resume_requested / resumed /
    refund_eligible_paused_requested; `request_source` student / support / sales."""

    __tablename__ = "enrollment_pause_log_new"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[int] = mapped_column(BigInteger)
    paused_at: Mapped[datetime | None]
    resumed_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(String(64))
    request_source: Mapped[str] = mapped_column(String(32))
    accepted: Mapped[int | None] = mapped_column(Integer)
    rejected: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None]


class WeekDay(PortalBase):
    __tablename__ = "week_days"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class StudentAvailability(PortalBase):
    __tablename__ = "student_week_day_availabilities"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    weekday_id: Mapped[int] = mapped_column(BigInteger)
    timezone: Mapped[str | None] = mapped_column(String(255))
    start_time: Mapped[time | None]
    end_time: Mapped[time | None]


class HeardAboutQuestion(PortalBase):
    """A source option ("Website", "YouTube") from the sign-up survey."""

    __tablename__ = "know_about_lawsikho_question"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(String(255))


class HeardAboutAnswer(PortalBase):
    __tablename__ = "know_about_lawsikho_student_answer"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    answer_id: Mapped[int] = mapped_column(BigInteger)


class StudentNote(PortalBase):
    """Metadata of an internal note. The body is not mapped -- see above."""

    __tablename__ = "students_internal_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]
