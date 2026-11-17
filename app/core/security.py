from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

bearer_scheme = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    id: int
    role: str
    token: str

    @property
    def is_admin(self) -> bool:
        return self.role in settings.admin_roles


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_sub": False, "verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Token has expired")
    except jwt.PyJWTError:
        raise _unauthorized("Invalid authentication token")


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> CurrentUser:
    """Validates the JWT and returns the caller. Ownership always comes from here,
    never from the request body."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Missing authentication token")
    payload = decode_token(credentials.credentials)
    raw_id = payload.get(settings.JWT_USER_ID_CLAIM)
    if raw_id is None:
        raw_id = payload.get("user_id", payload.get("id"))
    try:
        user_id = int(raw_id)
    except (TypeError, ValueError):
        raise _unauthorized("Token does not contain a valid user id")
    role = str(payload.get(settings.JWT_ROLE_CLAIM, "customer")).strip().lower()
    return CurrentUser(id=user_id, role=role, token=credentials.credentials)


def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator access required")
    return user


def create_service_token(expires_minutes: int = 5) -> str:
    """Short-lived admin-role token the Order Service uses for Product Catalog
    calls (reads and stock changes). It is never returned to clients."""
    admin_role = sorted(settings.admin_roles)[0] if settings.admin_roles else "admin"
    payload = {
        settings.JWT_USER_ID_CLAIM: settings.SERVICE_ACCOUNT_SUB,
        settings.JWT_ROLE_CLAIM: admin_role,
        "iss": "shopease-order-service",
        "exp": datetime.now(timezone.utc) + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
