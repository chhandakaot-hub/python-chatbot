from fastapi import APIRouter

from app.dependencies.auth import CurrentUser, DbSession
from app.schemas.user import UserOut, UserUpdate

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
def read_me(current_user: CurrentUser):
    return current_user


@router.patch("/me", response_model=UserOut)
def update_me(payload: UserUpdate, current_user: CurrentUser, db: DbSession):
    if payload.full_name is not None:
        current_user.full_name = payload.full_name
    db.commit()
    db.refresh(current_user)
    return current_user
