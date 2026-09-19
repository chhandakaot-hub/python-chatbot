from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    email: EmailStr
    full_name: str | None = None


class UserCreate(UserBase):
    password: str = Field(min_length=8, max_length=128)


class UserUpdate(BaseModel):
    full_name: str | None = None


class UserOut(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
