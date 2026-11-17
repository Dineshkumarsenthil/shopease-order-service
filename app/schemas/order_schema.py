from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.order_model import OrderStatus
from app.schemas.order_item_schema import OrderItemCreate, OrderItemOut


class OrderCreate(BaseModel):
    """Only items, address id and notes are accepted. Prices, totals and user id
    are never read from the client."""

    items: list[OrderItemCreate] = Field(min_length=1, max_length=50)
    shipping_address_id: int = Field(gt=0)
    notes: Optional[str] = Field(default=None, max_length=500)


class OrderStatusUpdate(BaseModel):
    order_status: OrderStatus


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    order_number: str
    user_id: int
    shipping_address: dict[str, Any]
    subtotal: Decimal
    discount_amount: Decimal
    shipping_charge: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    order_status: str
    payment_status: str
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemOut] = []


class OrderListOut(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[OrderOut]
