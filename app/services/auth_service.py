"""User registration and credential checking."""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.schemas.auth import Token
from app.schemas.user import UserCreate


def get_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email))


def register(db: Session, payload: UserCreate) -> User:
    if get_by_email(db, payload.email):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )
    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    user = get_by_email(db, email)
    # verify_password runs on a dummy hash when the user is missing, so a bad
    # email and a bad password take the same time to answer.
    stored = user.hashed_password if user else hash_password("__no_such_user__")
    if not verify_password(password, stored) or user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return user


def issue_token(user: User) -> Token:
    return Token(access_token=create_access_token(user.id))
