"""The portal's satisfaction surveys: assignment, class and evaluator CSAT, and NPS.

Mapped: who rated, what they rated, the 1-5 (CSAT) or 0-10 (NPS) rating, when.
The reasons a student ticked live in separate tables of fixed, staff-authored
options ("I found it very relevant to real life"), and those are safe to show.

Deliberately NOT mapped: every free-text field a student typed -- `other`,
`other_option`, `other_opinion`, `comment`, and NPS `reason`, `experience`,
`suggestions`. They hold personal circumstances and complaints about named
staff, and any text a person typed is a prompt-injection vector once it reaches
the model.

None of these models declare a relationship(): the services join explicitly.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.portal_database import PortalBase


class AssignmentCsat(PortalBase):
    __tablename__ = "assignment_csat_form"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    enrollment_id: Mapped[int | None] = mapped_column(BigInteger)
    assignment_id: Mapped[int] = mapped_column(BigInteger)
    course_id: Mapped[int] = mapped_column(BigInteger)
    batch_id: Mapped[int | None] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime | None]


class AssignmentCsatReason(PortalBase):
    __tablename__ = "assignment_csat_form_reasons"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(String(255))


class AssignmentCsatReasonMap(PortalBase):
    __tablename__ = "assignment_csat_form_reasons_mapping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    csat_id: Mapped[int] = mapped_column(BigInteger)
    reason_id: Mapped[int] = mapped_column(BigInteger)


class ClassCsat(PortalBase):
    __tablename__ = "class_csat_form"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    class_date_relation_id: Mapped[int] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(255))  # 'A' active, 'D' deactivated
    created_at: Mapped[datetime | None]


class ClassCsatReason(PortalBase):
    __tablename__ = "class_csat_form_reason"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(String(255))


class ClassCsatReasonMap(PortalBase):
    # The portal's own spelling: "maping".
    __tablename__ = "class_csat_form_reason_maping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    csat_id: Mapped[int] = mapped_column(BigInteger)
    reason_id: Mapped[int] = mapped_column(BigInteger)


class EvaluatorCsat(PortalBase):
    __tablename__ = "evaluator_csat_form"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    result_id: Mapped[int] = mapped_column(BigInteger)
    evaluator_id: Mapped[int] = mapped_column(BigInteger)
    rating: Mapped[int] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime | None]


class EvaluatorCsatReason(PortalBase):
    __tablename__ = "evaluator_csat_form_reason"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(String(255))


class EvaluatorCsatReasonMap(PortalBase):
    __tablename__ = "evaluator_csat_form_reason_maping"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    csat_id: Mapped[int] = mapped_column(BigInteger)
    reason_id: Mapped[int] = mapped_column(BigInteger)


class NpsForm(PortalBase):
    """Survey v1. `survey_type` is 'one_month' or 'completed'."""

    __tablename__ = "nps_form"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    enrollment_id: Mapped[int] = mapped_column(BigInteger)
    course_id: Mapped[int] = mapped_column(BigInteger)
    batch_id: Mapped[int] = mapped_column(BigInteger)
    survey_type: Mapped[str] = mapped_column(String(255))
    rating: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None]


class NpsFormV2(PortalBase):
    """Survey v2, sent at intervals: `survey_type` is a day count ("45", "90")."""

    __tablename__ = "nps_form_v2"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    student_id: Mapped[int] = mapped_column(BigInteger)
    survey_type: Mapped[str] = mapped_column(String(255))
    rating: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime | None]


class NpsReason(PortalBase):
    __tablename__ = "nps_form_reason"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    question: Mapped[str] = mapped_column(String(255))


class NpsV2ReasonMap(PortalBase):
    __tablename__ = "nps_form_reason_mapping_v2"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nps_id: Mapped[int] = mapped_column(BigInteger)
    reason_id: Mapped[int] = mapped_column(BigInteger)


class NpsCourseData(PortalBase):
    """Precomputed NPS per course, as the portal's own dashboard shows it."""

    __tablename__ = "nps_course_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    course_id: Mapped[int] = mapped_column(BigInteger)
    course_name: Mapped[str | None] = mapped_column(String(255))
    promoters: Mapped[int] = mapped_column(Integer)
    detractors: Mapped[int] = mapped_column(Integer)
    total_responses: Mapped[int] = mapped_column(Integer)
    total_students: Mapped[int] = mapped_column(Integer)
    nps: Mapped[float] = mapped_column(Float)


class NpsBootcampData(PortalBase):
    __tablename__ = "nps_bootcamp_data"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    bootcamp_id: Mapped[int] = mapped_column(BigInteger)
    bootcamp_name: Mapped[str | None] = mapped_column(String(255))
    promoters: Mapped[int] = mapped_column(Integer)
    detractors: Mapped[int] = mapped_column(Integer)
    total_responses: Mapped[int] = mapped_column(Integer)
    total_students: Mapped[int] = mapped_column(Integer)
    nps: Mapped[float] = mapped_column(Float)
