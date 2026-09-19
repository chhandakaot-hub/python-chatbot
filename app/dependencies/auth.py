"""Shared FastAPI dependencies for authenticated routes."""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.portal_database import get_portal_db
from app.core.security import credentials_error, decode_access_token
from app.models.user import User

# Points at the form endpoint, so the Swagger "Authorize" button works.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/token")

DbSession = Annotated[Session, Depends(get_db)]
PortalDbSession = Annotated[Session, Depends(get_portal_db)]


def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    db: DbSession,
) -> User:
    claims = decode_access_token(token)
    try:
        user_id = int(claims["sub"])
    except (KeyError, ValueError):
        raise credentials_error from None

    user = db.get(User, user_id)
    if user is None:
        raise credentials_error
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
