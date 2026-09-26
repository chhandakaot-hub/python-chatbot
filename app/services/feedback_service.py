"""Read-only queries over the portal's satisfaction surveys.

Four survey kinds share one query path, described once in `KINDS`:

    assignment_csat   1-5, per assignment a student handed in
    class_csat        1-5, per live class
    evaluator_csat    1-5, per evaluated result
    nps               0-10, course survey v1 ("one_month" / "completed")
    nps_v2            0-10, interval survey v2 (survey_type is a day count)

Same rules as the other portal services: SELECTs over allow-listed columns,
capped lists, bound parameters, and one filter builder shared by search, count,
summary and breakdown so they cannot disagree. Free-text answers are never
selected -- see `models/portal/feedback.py`.

A filter a kind does not have (`course_id` on class_csat) is an error, not
ignored: a dropped filter would turn "class ratings for this course" into the
average over every class, and the answer would look fine.
"""

from dataclasses import dataclass, field
from fastapi import HTTPException, status as http_status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.orm import Session

from app.models.portal import feedback as m
from app.models.portal.codes import CSAT_DEACTIVE, NPS_DETRACTOR_MAX, NPS_PROMOTER_MIN
from app.models.portal.course import Course
from app.models.portal.staff import Staff
from app.services.portal_service import MAX_RESULTS


@dataclass(frozen=True)
class Kind:
    model: type
    scale: str
    is_nps: bool = False
    # logical filter name -> column; also the extra ids shown on each row
    columns: dict[str, str] = field(default_factory=dict)
    reason: type | None = None
    reason_map: type | None = None
    reason_map_fk: str = "csat_id"


KINDS: dict[str, Kind] = {
    "assignment_csat": Kind(
        m.AssignmentCsat,
        "1-5",
        columns={k: k for k in ("enrollment_id", "assignment_id", "course_id", "batch_id")},
        reason=m.AssignmentCsatReason,
        reason_map=m.AssignmentCsatReasonMap,
    ),
    "class_csat": Kind(
        m.ClassCsat,
        "1-5",
        columns={"class_date_relation_id": "class_date_relation_id"},
        reason=m.ClassCsatReason,
        reason_map=m.ClassCsatReasonMap,
    ),
    "evaluator_csat": Kind(
        m.EvaluatorCsat,
        "1-5",
        columns={"result_id": "result_id", "evaluator_id": "evaluator_id"},
        reason=m.EvaluatorCsatReason,
        reason_map=m.EvaluatorCsatReasonMap,
    ),
    "nps": Kind(
        m.NpsForm,
        "0-10",
        is_nps=True,
        columns={k: k for k in ("enrollment_id", "course_id", "batch_id", "survey_type")},
    ),
    "nps_v2": Kind(
        m.NpsFormV2,
        "0-10",
        is_nps=True,
        columns={"survey_type": "survey_type"},
        reason=m.NpsReason,
        reason_map=m.NpsV2ReasonMap,
        reason_map_fk="nps_id",
    ),
}

# Filters every kind has.
_COMMON = ("student_id", "min_rating", "max_rating", "created_after", "created_before")


def _kind(name: str) -> Kind:
    kind = KINDS.get(name)
    if kind is None:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown feedback kind {name!r}. Allowed: {', '.join(sorted(KINDS))}.",
        )
    return kind


def allowed_filters(kind_name: str) -> set[str]:
    return set(_COMMON) | set(_kind(kind_name).columns)


def build_filters(kind_name: str, **criteria) -> list[ColumnElement[bool]]:
    """Optional criteria as SQL conditions, ANDed by the caller."""
    kind = _kind(kind_name)
    model = kind.model
    given = {k: v for k, v in criteria.items() if v is not None and v != ""}
    unsupported = set(given) - allowed_filters(kind_name)
    if unsupported:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{kind_name} has no filter(s) {', '.join(sorted(unsupported))}. "
                f"Allowed: {', '.join(sorted(allowed_filters(kind_name)))}."
            ),
        )

    conditions: list[ColumnElement[bool]] = []
    if hasattr(model, "status"):
        # A deactivated response was withdrawn; it is not feedback any more.
        conditions.append(func.coalesce(model.status, "") != CSAT_DEACTIVE)
    if kind.is_nps:
        conditions.append(model.rating.is_not(None))

    for name, value in given.items():
        if name == "min_rating":
            conditions.append(model.rating >= value)
        elif name == "max_rating":
            conditions.append(model.rating <= value)
        elif name == "created_after":
            conditions.append(model.created_at >= value)
        elif name == "created_before":
            conditions.append(model.created_at < value)
        elif name == "survey_type":
            conditions.append(model.survey_type == str(value))
        else:
            conditions.append(getattr(model, name) == value)
    return conditions


def _reasons_for(db: Session, kind: Kind, ids: list[int]) -> dict[int, list[str]]:
    """The ticked reason options for these responses, in one query."""
    if not ids or kind.reason is None:
        return {}
    fk = getattr(kind.reason_map, kind.reason_map_fk)
    rows = db.execute(
        select(fk, kind.reason.question)
        .select_from(kind.reason_map)
        .join(kind.reason, kind.reason.id == kind.reason_map.reason_id)
        .where(fk.in_(ids))
    )
    out: dict[int, list[str]] = {}
    for response_id, question in rows:
        out.setdefault(response_id, []).append(question)
    return out


def search_feedback(db: Session, kind_name: str, limit: int = 10, **criteria) -> list[dict]:
    """Newest responses matching every criterion, capped at MAX_RESULTS."""
    kind = _kind(kind_name)
    model = kind.model
    conditions = build_filters(kind_name, **criteria)
    columns = [model.id, model.student_id, model.rating, model.created_at]
    columns += [getattr(model, c) for c in kind.columns]
    statement = (
        select(*columns)
        .where(*conditions)
        .order_by(model.created_at.desc(), model.id.desc())
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    rows = [dict(r._mapping) for r in db.execute(statement)]
    reasons = _reasons_for(db, kind, [r["id"] for r in rows])
    for row in rows:
        row["kind"] = kind_name
        if kind.reason is not None:
            row["reasons"] = reasons.get(row["id"], [])
    return rows


def count_feedback(db: Session, kind_name: str, **criteria) -> int:
    kind = _kind(kind_name)
    return db.scalar(
        select(func.count(kind.model.id)).where(*build_filters(kind_name, **criteria))
    ) or 0


def feedback_summary(db: Session, kind_name: str, **criteria) -> dict:
    """Response count, average rating and the spread; for NPS also the score."""
    kind = _kind(kind_name)
    model = kind.model
    conditions = build_filters(kind_name, **criteria)

    count, average = db.execute(
        select(func.count(model.id), func.avg(model.rating)).where(*conditions)
    ).one()
    distribution = {
        rating: n
        for rating, n in db.execute(
            select(model.rating, func.count(model.id))
            .where(*conditions)
            .group_by(model.rating)
            .order_by(model.rating)
        )
    }
    summary: dict = {
        "kind": kind_name,
        "scale": kind.scale,
        "responses": count,
        "average_rating": round(float(average), 2) if average is not None else None,
        "distribution": distribution,
    }
    if kind.is_nps:
        promoters = sum(n for r, n in distribution.items() if r >= NPS_PROMOTER_MIN)
        detractors = sum(n for r, n in distribution.items() if r <= NPS_DETRACTOR_MAX)
        summary.update(
            promoters=promoters,
            passives=count - promoters - detractors,
            detractors=detractors,
            nps=round((promoters - detractors) / count * 100, 1) if count else None,
        )
    return summary


def _month(model):
    return func.date_format(model.created_at, "%Y-%m")


def breakdown_feedback(
    db: Session,
    kind_name: str,
    by: str,
    limit: int = 10,
    sort: str = "count",
    min_responses: int | None = None,
    **criteria,
) -> list[dict]:
    """Response counts (and average rating) grouped by one dimension.

    `by` is looked up in an allow-list before any database access. `sort` is
    "count" (biggest group first, the default), "average_rating" (best rated
    first) or "lowest_rating" (worst rated first). Ranking by rating needs
    `min_responses`: without a floor a group with a single 5-star response
    beats one with a hundred 4.8s, and one with a single 1-star sinks to the
    bottom.
    """
    if sort not in {"count", "average_rating", "lowest_rating"}:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="sort must be 'count', 'average_rating' or 'lowest_rating'.",
        )
    kind = _kind(kind_name)
    model = kind.model
    dimensions: dict[str, ColumnElement] = {
        "rating": model.rating,
        "month": _month(model),
    }
    for name in ("survey_type", "course_id", "batch_id", "evaluator_id"):
        if name in kind.columns:
            dimensions[name] = getattr(model, name)
    if "course_id" in kind.columns:
        dimensions["course"] = Course.course_name
    if "evaluator_id" in kind.columns:
        dimensions["evaluator"] = Staff.full_name
    if kind.reason is not None:
        dimensions["reason"] = kind.reason.question

    if by not in dimensions:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot group {kind_name} by {by!r}. Allowed: {', '.join(sorted(dimensions))}.",
        )

    dimension = dimensions[by]
    statement = select(
        dimension.label("value"),
        func.count(model.id).label("count"),
        func.avg(model.rating).label("average_rating"),
    ).select_from(model)
    if by == "course":
        statement = statement.join(Course, Course.id == model.course_id)
    elif by == "evaluator":
        statement = statement.join(Staff, Staff.id == model.evaluator_id)
    elif by == "reason":
        fk = getattr(kind.reason_map, kind.reason_map_fk)
        statement = statement.join(kind.reason_map, fk == model.id).join(
            kind.reason, kind.reason.id == kind.reason_map.reason_id
        )

    if sort == "average_rating":
        order = [func.avg(model.rating).desc(), func.count(model.id).desc()]
    elif sort == "lowest_rating":
        order = [func.avg(model.rating).asc(), func.count(model.id).desc()]
    elif by == "month":
        order = [dimension.desc()]
    else:
        order = [func.count(model.id).desc()]
    statement = (
        statement.where(*build_filters(kind_name, **criteria))
        .group_by(dimension)
        .order_by(*order)
        .limit(max(1, min(limit, MAX_RESULTS)))
    )
    if min_responses is not None and min_responses > 1:
        statement = statement.having(func.count(model.id) >= min_responses)
    return [
        {
            "value": value.strip() if isinstance(value, str) else value,
            "count": n,
            "average_rating": round(float(avg), 2) if avg is not None else None,
        }
        for value, n, avg in db.execute(statement)
    ]


def nps_scores(db: Session, scope: str, name: str | None = None, limit: int = 10) -> list[dict]:
    """The portal's precomputed NPS per course or per bootcamp."""
    if scope == "course":
        model, name_col, id_col = m.NpsCourseData, m.NpsCourseData.course_name, "course_id"
    elif scope == "bootcamp":
        model, name_col, id_col = m.NpsBootcampData, m.NpsBootcampData.bootcamp_name, "bootcamp_id"
    else:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="scope must be 'course' or 'bootcamp'.",
        )
    statement = select(model)
    if name and name.strip():
        statement = statement.where(name_col.like(f"%{name.strip()}%"))
    statement = statement.order_by(model.nps.desc()).limit(max(1, min(limit, MAX_RESULTS)))
    return [
        {
            "id": getattr(r, id_col),
            "name": r.course_name if scope == "course" else r.bootcamp_name,
            "promoters": r.promoters,
            "detractors": r.detractors,
            "responses": r.total_responses,
            "students": r.total_students,
            "nps": round(r.nps, 1),
        }
        for r in db.scalars(statement)
    ]
