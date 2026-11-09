from typing import Generator

import requests
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from pydantic import ValidationError
from sqlalchemy.orm import Session

from models import User
from crud import user

from core import security
from core.config import settings
from db.session import SessionLocal

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/login/access-token/")


class AuthenticatedToken:
    """
    Result of a successful GateKeeper-backed authentication.

    Wraps the raw bearer token (still needed verbatim for pass-through
    calls to other OpenAgri services, e.g. ``Authorization: Bearer
    {token}``) together with the identity claim(s) extracted from a
    signature-VERIFIED decode of that same token.

    GateKeeper's ``api/validate_token/`` response body does not carry the
    ``user_id`` claim (only ``{"success": ..., "remaining_time_in_seconds":
    ...}``), so we cannot read identity off it directly. We still must not
    trust an unverified decode for identity/authorization decisions, so we
    perform our own signature-verified decode (using the shared
    JWT_SIGNING_KEY) once here, and callers use `.id`/`.user_id` from that
    verified result instead of re-decoding the token themselves.

    `.id` mirrors the attribute name used by the local `User` DB model
    (returned on the non-GateKeeper path) so callers can use `token.id`
    regardless of which authentication mode is active.
    """

    def __init__(self, token: str, user_id: str):
        self.token = token
        self.user_id = user_id
        self.id = user_id

    def __str__(self) -> str:
        return self.token

    def __format__(self, format_spec: str) -> str:
        return format(self.token, format_spec)


def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


def get_current_user(
    db: Session = Depends(get_db), token: str = Depends(reusable_oauth2)
):
    try:
        if settings.REPORTING_USING_GATEKEEPER:
            try:
                response = requests.post(
                    url=settings.REPORTING_GATEKEEPER_BASE_URL
                    + "api/validate_token/",
                    headers={"Content-Type": "application/json"},
                    json={"token": token, "token_type": "access"},
                    timeout=settings.REPORTING_GATEKEEPER_TIMEOUT_SECONDS,
                )
            except requests.exceptions.Timeout:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail="Timed out validating token with GateKeeper.",
                )
            except requests.exceptions.RequestException:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="GateKeeper is unreachable; could not validate token.",
                )

            if str(response.status_code)[0] != "2":
                raise HTTPException(status_code=400, detail="Error, bad jwt token.")

            payload = jwt.decode(
                token, settings.JWT_SIGNING_KEY, algorithms=[security.ALGORITHM]
            )
            user_id = payload.get("user_id")
            if not user_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Token is missing the user_id claim.",
                )
            return AuthenticatedToken(token=token, user_id=user_id)
        else:
            payload = jwt.decode(
                token, settings.JWT_SIGNING_KEY, algorithms=[security.ALGORITHM]
            )
            user_db = user.get(db, id=payload["sub"])
            if not user_db:
                raise HTTPException(status_code=404, detail="User not found")
            return user_db

    except (jwt.PyJWTError, ValidationError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
