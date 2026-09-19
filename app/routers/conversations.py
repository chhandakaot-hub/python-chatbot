from fastapi import APIRouter, status

from app.dependencies.auth import CurrentUser, DbSession
from app.schemas.conversation import ConversationDetail, ConversationOut
from app.services import conversation_service

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=list[ConversationOut])
def list_conversations(current_user: CurrentUser, db: DbSession):
    return conversation_service.list_for_user(db, current_user.id)


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(conversation_id: int, current_user: CurrentUser, db: DbSession):
    return conversation_service.get_owned(db, conversation_id, current_user.id)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(conversation_id: int, current_user: CurrentUser, db: DbSession) -> None:
    conversation_service.delete(db, conversation_id, current_user.id)
