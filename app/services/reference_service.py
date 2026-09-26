"""Read-only lookups over the portal's small reference tables.

One entry point, `lookup()`, because these all answer the same question: "what
is the id / name of X". `kind` is looked up in an allow-list.

`lookup()` returns at most MAX_RESULTS rows; `count()` returns how many
matched. Both build their statement with `_query()`, so a list and its count
cannot disagree -- and a caller told only "25 returned" is never left guessing
at a total (asked how many states India has, the model paged through letter
searches and answered 32; there are 36).
"""

import json

from fastapi import HTTPException, status as http_status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.models.portal.reference import Country, CourseCategory, JobRole, State, Tag
from app.services.portal_service import MAX_RESULTS

KINDS = ("country", "state", "tag", "course_category", "job_role")


def _tag_name(raw: str) -> str:
    """Tag names are stored as translation JSON -- {"en": "Batch Migrated"}."""
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if isinstance(decoded, dict):
        return str(decoded.get("en") or next(iter(decoded.values()), raw))
    return raw


def _validate(kind: str, country_id: int | None, parent_id: int | None) -> None:
    if kind not in KINDS:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown kind {kind!r}. Allowed: {', '.join(KINDS)}.",
        )
    if country_id is not None and kind != "state":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST, detail="country_id only applies to states."
        )
    if parent_id is not None and kind != "course_category":
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="parent_id only applies to course categories.",
        )


# What each kind counts, and how it is ordered when listed.
_COUNTED = {
    "country": Country.id,
    "state": State.id,
    "tag": Tag.id,
    "course_category": CourseCategory.id,
    "job_role": JobRole.id,
}
_ORDER = {
    "country": Country.name,
    "state": State.name,
    "tag": Tag.id,
    "course_category": CourseCategory.category_name,
    "job_role": JobRole.title,
}


def _query(
    kind: str,
    columns: tuple,
    query: str | None,
    country_id: int | None,
    parent_id: int | None,
) -> Select:
    """The filtered statement for one kind, selecting whatever `columns` says."""
    like = f"%{query.strip()}%" if query and query.strip() else None

    if kind == "country":
        statement = select(*columns).select_from(Country)
        if like:
            statement = statement.where(
                Country.name.like(like) | Country.common_name.like(like) | Country.short.like(like)
            )
    elif kind == "state":
        statement = select(*columns).select_from(State).join(Country, Country.id == State.country_id)
        if like:
            statement = statement.where(State.name.like(like))
        if country_id is not None:
            statement = statement.where(State.country_id == country_id)
    elif kind == "tag":
        statement = select(*columns).select_from(Tag).where(
            Tag.deleted_at.is_(None), Tag.status == 1
        )
        if like:
            statement = statement.where(Tag.name.like(like))
    elif kind == "course_category":
        statement = (
            select(*columns).select_from(CourseCategory).where(CourseCategory.deleted_at.is_(None))
        )
        if like:
            statement = statement.where(CourseCategory.category_name.like(like))
        if parent_id is not None:
            statement = statement.where(CourseCategory.parent_id == parent_id)
    else:
        statement = select(*columns).select_from(JobRole)
        if like:
            statement = statement.where(JobRole.title.like(like))
    return statement


def count(
    db: Session,
    kind: str,
    query: str | None = None,
    country_id: int | None = None,
    parent_id: int | None = None,
) -> int:
    """True number of matches -- not the capped list length."""
    _validate(kind, country_id, parent_id)
    statement = _query(kind, (func.count(_COUNTED[kind]),), query, country_id, parent_id)
    return db.scalar(statement) or 0


def lookup(
    db: Session,
    kind: str,
    query: str | None = None,
    country_id: int | None = None,
    parent_id: int | None = None,
    limit: int = 10,
) -> list[dict]:
    _validate(kind, country_id, parent_id)
    limit = max(1, min(limit, MAX_RESULTS))

    if kind == "country":
        columns: tuple = (Country,)
    elif kind == "state":
        columns = (State.id, State.name, State.country_id, Country.name)
    elif kind == "tag":
        columns = (Tag,)
    elif kind == "course_category":
        columns = (CourseCategory,)
    else:
        columns = (JobRole,)

    statement = _query(kind, columns, query, country_id, parent_id).order_by(_ORDER[kind]).limit(limit)

    if kind == "country":
        return [
            {"id": c.id, "name": c.name, "code": c.short, "phone_code": c.phone_code}
            for c in db.scalars(statement)
        ]
    if kind == "state":
        return [
            {"id": i, "name": n, "country_id": cid, "country": cn}
            for i, n, cid, cn in db.execute(statement)
        ]
    if kind == "tag":
        return [{"id": t.id, "name": _tag_name(t.name), "type": t.type} for t in db.scalars(statement)]
    if kind == "course_category":
        return [
            {
                "id": c.id,
                "name": c.category_name,
                "type": c.type,
                "parent_id": c.parent_id,
                "active": bool(c.status),
            }
            for c in db.scalars(statement)
        ]
    return [{"id": j.id, "title": j.title} for j in db.scalars(statement)]
