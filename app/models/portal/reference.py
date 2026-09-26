"""Small lookup tables: countries, states, tags, course categories, job roles.

Only names and the ids that link them. Staff ids and timestamps are left out.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class Country(PortalBase):
    __tablename__ = "countries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    short: Mapped[str] = mapped_column(String(2))
    name: Mapped[str] = mapped_column(String(80))
    common_name: Mapped[str] = mapped_column(String(80))
    phone_code: Mapped[int] = mapped_column(Integer)


class State(PortalBase):
    __tablename__ = "states"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    country_id: Mapped[int] = mapped_column(BigInteger)


class Tag(PortalBase):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    type: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None]


class CourseCategory(PortalBase):
    __tablename__ = "course_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    parent_id: Mapped[int | None] = mapped_column(Integer)
    category_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None]


class JobRole(PortalBase):
    __tablename__ = "job_roles"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255))
