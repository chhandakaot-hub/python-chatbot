"""Password hashing and JWT signing.

Uses only the standard library (PBKDF2-SHA256 + HMAC-SHA256) so the project
runs with no extra dependencies. Swapping in passlib / python-jose later means
changing these four functions and nothing else.
"""

import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status

from app.core.config import settings

_PBKDF2_ROUNDS = 260_000

credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


# --------------------------------------------------------------------------- #
# Passwords — stored as "pbkdf2_sha256$rounds$salt$hash"
# --------------------------------------------------------------------------- #
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ROUNDS)
    return f"pbkdf2_sha256${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), expected)


# --------------------------------------------------------------------------- #
# JWT (HS256)
# --------------------------------------------------------------------------- #
def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _sign(signing_input: str) -> str:
    return _b64url(
        hmac.new(settings.SECRET_KEY.encode(), signing_input.encode(), hashlib.sha256).digest()
    )


def create_access_token(subject: str | int, expires_minutes: int | None = None) -> str:
    expires = datetime.now(UTC) + timedelta(
        minutes=expires_minutes or settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    header = _b64url(json.dumps({"alg": settings.ALGORITHM, "typ": "JWT"}).encode())
    payload = _b64url(json.dumps({"sub": str(subject), "exp": int(expires.timestamp())}).encode())
    return f"{header}.{payload}.{_sign(f'{header}.{payload}')}"


def decode_access_token(token: str) -> dict:
    try:
        header, payload, signature = token.split(".")
    except ValueError:
        raise credentials_error from None

    if not hmac.compare_digest(signature, _sign(f"{header}.{payload}")):
        raise credentials_error

    try:
        claims = json.loads(_b64url_decode(payload))
    except ValueError:
        raise credentials_error from None

    if claims.get("exp", 0) < datetime.now(UTC).timestamp():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return claims
