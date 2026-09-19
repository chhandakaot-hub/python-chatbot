from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StudentOut(BaseModel):
    """What the API returns for a portal student.

    Mirrors the mapped columns on `Student`. Two layers say the same thing on
    purpose: the model decides what is read from the database, this decides
    what leaves the application.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    reg_code: str
    full_name: str
    email: str
    status: int

    city: str | None = None
    state: str | None = None
    country: str | None = None
    what_are_you_doing_currently: str | None = None

    enrollment_form_filled_at: datetime | None = None
    email_verified_at: datetime | None = None
    last_login: datetime | None = None
    created_at: datetime | None = None


class StudentSummary(BaseModel):
    """The lighter shape used for search results and, later, for AI context."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    reg_code: str
    full_name: str
    email: str
    status: int
