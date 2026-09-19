from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.message import Role


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    role: Role
    content: str
    created_at: datetime


class ConversationCreate(BaseModel):
    title: str = Field(default="New conversation", max_length=255)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut] = []
