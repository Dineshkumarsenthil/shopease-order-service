import logging
from typing import Any

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import CurrentUser
from app.services import http_client

log = logging.getLogger(__name__)


def _unwrap(body: Any) -> dict:
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body["data"]
    return body if isinstance(body, dict) else {}


def get_shipping_address(address_id: int, user: CurrentUser) -> dict:
    """Fetch the address with the CUSTOMER's own token, so the User Service itself
    refuses addresses that belong to someone else. We also double-check user_id
    when the response contains it."""
    url = settings.USER_SERVICE_URL.rstrip("/") + settings.USER_ADDRESS_PATH.format(address_id=address_id)
    try:
        with http_client.new_client() as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {user.token}"})
    except httpx.HTTPError as exc:
        log.error("User Service unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="User Service is unavailable, please try again later")

    if resp.status_code in (403, 404):
        raise HTTPException(status_code=404, detail="Shipping address not found for this user")
    if resp.status_code == 401:
        raise HTTPException(status_code=401, detail="Token was rejected by the User Service")
    if resp.status_code >= 500:
        raise HTTPException(status_code=503, detail="User Service is unavailable, please try again later")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Unexpected response from User Service ({resp.status_code})")

    address = _unwrap(resp.json())
    owner = address.get("user_id")
    if owner is not None and int(owner) != user.id:
        raise HTTPException(status_code=404, detail="Shipping address not found for this user")
    return address


def get_profile_snapshot(user: CurrentUser) -> dict:
    """Best-effort customer profile (name/email/phone). A failure here never blocks an order."""
    if not settings.USER_PROFILE_PATH:
        return {}
    url = settings.USER_SERVICE_URL.rstrip("/") + settings.USER_PROFILE_PATH
    try:
        with http_client.new_client() as client:
            resp = client.get(url, headers={"Authorization": f"Bearer {user.token}"})
        if resp.status_code != 200:
            return {}
        profile = _unwrap(resp.json())
    except (httpx.HTTPError, ValueError):
        return {}
    name = profile.get("full_name") or profile.get("name") or profile.get("username")
    snapshot = {"name": name, "email": profile.get("email"), "phone": profile.get("phone")}
    return {k: v for k, v in snapshot.items() if v}
