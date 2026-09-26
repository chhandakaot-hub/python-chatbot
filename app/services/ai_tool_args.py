"""Schema builders and argument coercion shared by the Gemini tool modules.

Everything a tool receives was written by the model, so nothing here trusts
its input: a bad date is dropped rather than raised, and numbers arrive as
whatever type the model felt like.
"""

from datetime import date
from typing import Any

from google.genai import types


def _iso_date(description: str) -> types.Schema:
    return types.Schema(type=types.Type.STRING, description=f"ISO date (YYYY-MM-DD); {description}.")


_LIMIT = types.Schema(type=types.Type.INTEGER, description="Maximum rows to return (1-25, default 10).")


# Appended to every list tool: without it the model reads "25 returned" as "25
# exist" -- it answered 32 for India's 36 states and 70 for 143 bootcamps.
_TOTAL_NOTE = (
    "Returns at most 25 rows; total_matching is the true number of matches, so "
    "report that -- never the length of the returned list."
)


def _object(properties: dict[str, types.Schema], required: list[str] | None = None) -> types.Schema:
    return types.Schema(type=types.Type.OBJECT, properties=properties, required=required or None)


def _parse_date(value: Any) -> date | None:
    """A bad date must not raise; it is simply not applied."""
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _int(value: Any) -> int | None:
    return None if value is None or value == "" else int(value)


def _float(value: Any) -> float | None:
    return None if value is None or value == "" else float(value)


def _bool(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes"}


def _limit(args: dict[str, Any]) -> int:
    return _int(args.get("limit")) or 10
