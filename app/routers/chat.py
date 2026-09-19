from fastapi import APIRouter

from app.dependencies.auth import CurrentUser, DbSession
from app.models.message import Message
from app.schemas.chat import ChatRequest, ChatResponse
from app.schemas.conversation import MessageOut
from app.services import conversation_service, gemini_service

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post("", response_model=ChatResponse)
async def send_message(
    payload: ChatRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> ChatResponse:
    """Send a message and get Gemini's reply, persisting both turns."""
    if payload.conversation_id is None:
        conversation = conversation_service.create(
            db, current_user.id, conversation_service.suggest_title(payload.message)
        )
        history: list[Message] = []
    else:
        conversation = conversation_service.get_owned(
            db, payload.conversation_id, current_user.id
        )
        history = list(conversation.messages)

    # Nothing is committed until the model answers, so a provider failure
    # leaves no half-written conversation behind.
    reply_text = await gemini_service.generate_reply(history, payload.message)
    reply = conversation_service.add_exchange(db, conversation, payload.message, reply_text)

    return ChatResponse(
        conversation_id=conversation.id,
        reply=MessageOut.model_validate(reply),
    )
