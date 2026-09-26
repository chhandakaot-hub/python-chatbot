"""The portal's product catalogue: packages, bootcamps, books and book deliveries.

Deliberately NOT mapped:

* `book_delivery_log`'s personal columns -- `student_name`, `student_email`,
  `phone_number`, `address`, `pin_code`, `student_code` -- and its free-text
  `comment`. A delivery question is answered by the book, the enrollment and
  the status; a postal address has no business in a prompt. City, state and
  country stay: the students table already exposes the same three.
* `packages.image_path`, staff ids (`created_by`, `updated_by`).
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class Package(PortalBase):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    duration_days: Mapped[int] = mapped_column(Integer)
    deleted_at: Mapped[datetime | None]


class PackageCourse(PortalBase):
    __tablename__ = "package_course_mappings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    package_id: Mapped[int] = mapped_column(BigInteger)
    course_id: Mapped[int] = mapped_column(BigInteger)


class Bootcamp(PortalBase):
    __tablename__ = "bootcamps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(255))
    # NOT a yes/no flag. The portal reads it as `$limit`: how many of a
    # bootcamp's courses may carry the refund-eligible tag ("Only N course(s) can
    # be refund eligible in this bootcamp", EnrollmentTrait.php). Defaults to 1;
    # 143 bootcamps have 1 and 3 have 2, so every bootcamp allows at least one.
    refund_eligible_course: Mapped[int] = mapped_column(Integer)


class Book(PortalBase):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    sku: Mapped[str] = mapped_column(String(255))
    is_send_able: Mapped[int] = mapped_column(Integer)  # Book.php SEND_ABLE = 1


class CourseBook(PortalBase):
    __tablename__ = "course_books"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger)
    course_id: Mapped[int] = mapped_column(BigInteger)
    delivery_start_date: Mapped[date | None]


class BootcampBook(PortalBase):
    __tablename__ = "bootcamp_books"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    book_id: Mapped[int] = mapped_column(BigInteger)
    bootcamp_id: Mapped[int] = mapped_column(BigInteger)
    delivery_start_date: Mapped[date | None]


class BookDelivery(PortalBase):
    """One book owed to one enrollment. See the module docstring for what is left out."""

    __tablename__ = "book_delivery_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    enrollment_id: Mapped[int | None] = mapped_column(BigInteger)
    book_id: Mapped[int | None] = mapped_column(BigInteger)
    bootcamp_id: Mapped[int | None] = mapped_column(BigInteger)
    student_id: Mapped[int | None] = mapped_column(BigInteger)
    course_id: Mapped[int | None] = mapped_column(BigInteger)
    course_type: Mapped[str | None] = mapped_column(String(255))  # 'NORMAL COURSE' / 'BOOTCAMP'
    batch: Mapped[str | None] = mapped_column(String(255))
    course_or_bootcamp_name: Mapped[str | None] = mapped_column(String(255))
    sku: Mapped[str | None] = mapped_column(String(255))
    book_name: Mapped[str | None] = mapped_column(String(255))
    city: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str | None] = mapped_column(String(255))
    country: Mapped[str | None] = mapped_column(String(255))
    is_sent: Mapped[int] = mapped_column(Integer)  # SENT = 1
    sent_on: Mapped[date | None]
    is_deliverable: Mapped[int] = mapped_column(Integer)  # DELIVERABLE = 1
    is_additional: Mapped[str] = mapped_column(String(255))  # 'IS ADDITIONAL' / 'IS NOT ADDITIONAL'
    generation_type: Mapped[int] = mapped_column(Integer)  # MANUAL = 1, SYSTEM = 0
    created_at: Mapped[datetime | None]
