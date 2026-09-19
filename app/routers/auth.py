from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.dependencies.auth import DbSession
from app.schemas.auth import LoginRequest, Token
from app.schemas.user import UserCreate, UserOut
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession):
    return auth_service.register(db, payload)


@router.post("/login", response_model=Token)
def login(payload: LoginRequest, db: DbSession) -> Token:
    """JSON login — what a frontend should call."""
    user = auth_service.authenticate(db, payload.email, payload.password)
    return auth_service.issue_token(user)


@router.post("/token", response_model=Token)
def login_form(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> Token:
    """Form login, for the Swagger UI "Authorize" button.

    OAuth2 calls the field `username`; put your email address in it.
    """
    user = auth_service.authenticate(db, form.username, form.password)
    return auth_service.issue_token(user)
