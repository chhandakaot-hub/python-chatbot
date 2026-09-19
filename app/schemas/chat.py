from pydantic import BaseModel, Field

from app.schemas.conversation import MessageOut


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: int | None = Field(
        default=None,
        description="Continue an existing conversation; omit to start a new one.",
    )


class ChatResponse(BaseModel):
    conversation_id: int
    reply: MessageOut
