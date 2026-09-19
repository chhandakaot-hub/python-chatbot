"""Conversation and message persistence."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.message import Message, Role


def list_for_user(db: Session, user_id: int) -> list[Conversation]:
    return list(
        db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc())
        )
    )


def get_owned(db: Session, conversation_id: int, user_id: int) -> Conversation:
    """Fetch a conversation, 404-ing if it is missing *or* owned by someone else."""
    conversation = db.get(Conversation, conversation_id)
    if conversation is None or conversation.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found"
        )
    return conversation


def create(db: Session, user_id: int, title: str) -> Conversation:
    conversation = Conversation(user_id=user_id, title=title)
    db.add(conversation)
    db.flush()  # assigns the id without ending the transaction
    return conversation


def delete(db: Session, conversation_id: int, user_id: int) -> None:
    conversation = get_owned(db, conversation_id, user_id)
    db.delete(conversation)
    db.commit()


def add_exchange(
    db: Session, conversation: Conversation, prompt: str, reply_text: str
) -> Message:
    """Persist the user turn and the assistant turn together."""
    db.add(Message(conversation_id=conversation.id, role=Role.user, content=prompt))
    reply = Message(conversation_id=conversation.id, role=Role.assistant, content=reply_text)
    db.add(reply)
    db.commit()
    db.refresh(reply)
    return reply


def suggest_title(prompt: str, limit: int = 60) -> str:
    """Cheap local title for a brand-new conversation — no extra API call."""
    title = " ".join(prompt.split())
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"
