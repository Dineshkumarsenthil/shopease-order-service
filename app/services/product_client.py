import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

import httpx
from fastapi import HTTPException

from app.core.config import settings
from app.core.security import create_service_token
from app.services import http_client

log = logging.getLogger(__name__)


@dataclass
class ProductInfo:
    id: int
    name: str
    sku: str
    price: Decimal
    is_active: bool
    stock: Optional[int]  # None when the Catalog does not expose stock on the product


class StockOperationError(Exception):
    """Raised when a stock call fails; `status_code` is what the API should return."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _headers() -> dict:
    return {"Authorization": f"Bearer {create_service_token()}"}


def _url(path: str, product_id: int) -> str:
    return settings.PRODUCT_SERVICE_URL.rstrip("/") + path.format(product_id=product_id)


def _first(data: dict, *keys, default=None):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def _parse_product(product_id: int, raw: dict) -> ProductInfo:
    data = raw.get("data") if isinstance(raw.get("data"), dict) else raw
    try:
        price = Decimal(str(_first(data, "price", "unit_price")))
    except (InvalidOperation, TypeError):
        raise HTTPException(status_code=502, detail="Product Catalog returned an invalid price")
    if "is_active" in data or "active" in data:
        active = bool(_first(data, "is_active", "active"))
    else:
        active = str(data.get("status", "ACTIVE")).upper() == "ACTIVE"
    stock = _first(data, "stock_quantity", "quantity", "stock", "available_stock", "stock_level")
    return ProductInfo(
        id=product_id,
        name=str(_first(data, "name", "product_name", default=f"Product {product_id}")),
        sku=str(_first(data, "sku", default="")),
        price=price,
        is_active=active,
        stock=int(stock) if stock is not None else None,
    )


def get_product(product_id: int) -> ProductInfo:
    try:
        with http_client.new_client() as client:
            resp = client.get(_url(settings.PRODUCT_DETAIL_PATH, product_id), headers=_headers())
    except httpx.HTTPError as exc:
        log.error("Product Catalog unreachable: %s", exc)
        raise HTTPException(status_code=503, detail="Product Catalog Service is unavailable, please try again later")
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail=f"Product {product_id} not found")
    if resp.status_code >= 500:
        raise HTTPException(status_code=503, detail="Product Catalog Service is unavailable, please try again later")
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Unexpected response from Product Catalog ({resp.status_code})")
    return _parse_product(product_id, resp.json())


def _stock_call(path: str, product_id: int, body: dict) -> None:
    try:
        with http_client.new_client() as client:
            resp = client.post(_url(path, product_id), json=body, headers=_headers())
    except httpx.HTTPError as exc:
        log.error("Stock call failed (%s): %s", body.get("reference"), exc)
        raise StockOperationError(503, "Product Catalog Service is unavailable, please try again later")
    if resp.status_code in (200, 201):
        return
    if resp.status_code == 404:
        raise StockOperationError(404, f"Product {product_id} not found")
    if resp.status_code in (400, 409, 422):
        raise StockOperationError(409, f"Insufficient stock for product {product_id}")
    if resp.status_code >= 500:
        raise StockOperationError(503, "Product Catalog Service is unavailable, please try again later")
    raise StockOperationError(502, f"Unexpected response from Product Catalog ({resp.status_code})")


def reserve_stock(product_id: int, quantity: int, reference: str) -> None:
    """Stock-out. The unique `reference` makes the call idempotent in the Catalog."""
    _stock_call(settings.STOCK_OUT_PATH, product_id, {"quantity": quantity, "reference": reference,
                                                    "reason": "Order reservation"})


def release_stock(product_id: int, quantity: int, reference: str, reverses_reference: str) -> None:
    """Stock-in. `reference` makes repeats harmless; `reverses_reference` tells the Catalog
    to restore only if that original reservation really happened."""
    _stock_call(settings.STOCK_IN_PATH, product_id, {"quantity": quantity, "reference": reference,
                                                   "reverses_reference": reverses_reference,
                                                   "reason": "Order stock release"})
