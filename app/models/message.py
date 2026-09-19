import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Role(str, enum.Enum):
    system = "system"
    user = "user"
    assistant = "assistant"


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")  # noqa: F821

    def __repr__(self) -> str:
        return f"<Message id={self.id} role={self.role.value}>"
