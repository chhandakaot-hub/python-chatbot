"""The portal's staff: `users`, their roles, and who is attached to which course.

Students are a different table (`students`). A "user" here is an admin,
evaluator, performance coach and so on.

Deliberately NOT mapped from `users`: `email`, `phone`, `password`,
`remember_token`, the verification tokens, the calendar/meeting links and ids,
the forum token, and the Edmingle/Kanboard ids. Staff are identified by name;
their contact details and credentials never leave the portal.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase

# model_has_roles.model_type for a staff account (there are no other kinds).
STAFF_MODEL_TYPE = "App\\Models\\User"


class Staff(PortalBase):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)  # see codes.UserStatus
    last_login: Mapped[datetime | None]
    created_at: Mapped[datetime | None]
    deleted_at: Mapped[datetime | None]


class Role(PortalBase):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None]


class StaffRole(PortalBase):
    """`model_has_roles`; its key is (role_id, model_type, model_id)."""

    __tablename__ = "model_has_roles"

    role_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_type: Mapped[str] = mapped_column(String(255), primary_key=True)
    model_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)


class CourseEvaluator(PortalBase):
    __tablename__ = "course_evaluator_mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(BigInteger)
    evaluator_id: Mapped[int] = mapped_column(BigInteger)


class CourseInstructor(PortalBase):
    __tablename__ = "course_instructor_mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(BigInteger)
    instructor_id: Mapped[int] = mapped_column(BigInteger)
    deleted_at: Mapped[datetime | None]


class CourseMentor(PortalBase):
    __tablename__ = "course_mentor_mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(BigInteger)
    mentor_id: Mapped[int] = mapped_column(BigInteger)
