"""Business logic for orders: pricing, creation with stock reservation/compensation,
retrieval, status changes and idempotent cancellation."""
import logging
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import CurrentUser
from app.core.utils import utcnow
from app.models.order_item_model import OrderItem
from app.models.order_model import (
    ALLOWED_TRANSITIONS,
    CANCELLABLE_STATUSES,
    Order,
    OrderStatus,
)
from app.schemas.order_schema import OrderCreate
from app.services import product_client, user_client
from app.services.product_client import StockOperationError

log = logging.getLogger(__name__)
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ------------------------------------------------------------------ pricing
@dataclass
class Line:
    product_id: int
    name: str
    sku: str
    unit_price: Decimal
    quantity: int
    discount: Decimal = Decimal("0.00")

    @property
    def gross(self) -> Decimal:
        return money(self.unit_price * self.quantity)

    @property
    def total(self) -> Decimal:
        return money(self.gross - self.discount)


@dataclass
class Totals:
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    shipping: Decimal
    total: Decimal


def calculate_totals(lines: list[Line]) -> Totals:
    """Subtotal - Discount + Tax + Shipping, all in Decimal, rules from settings.
    The order discount is split across the items so item totals add up exactly."""
    subtotal = money(sum((l.gross for l in lines), Decimal("0")))

    discount = Decimal("0.00")
    if subtotal >= settings.DISCOUNT_MIN_SUBTOTAL and settings.DISCOUNT_PERCENT > 0:
        discount = money(subtotal * settings.DISCOUNT_PERCENT / Decimal("100"))

    allocated = Decimal("0.00")
    for index, line in enumerate(lines):
        if index == len(lines) - 1:
            line.discount = money(discount - allocated)
        else:
            share = money(discount * line.gross / subtotal) if subtotal else Decimal("0.00")
            line.discount = share
            allocated += share

    taxable = subtotal - discount
    tax = money(taxable * settings.TAX_RATE)
    shipping = Decimal("0.00") if taxable >= settings.FREE_SHIPPING_THRESHOLD else money(settings.SHIPPING_CHARGE)
    total = money(subtotal - discount + tax + shipping)

    # Validation: saved items must reconcile with the order totals
    if sum((l.total for l in lines), Decimal("0")) != subtotal - discount:
        raise HTTPException(status_code=500, detail="Order total validation failed")
    if min(subtotal, discount, tax, shipping, total) < 0 or total <= 0:
        raise HTTPException(status_code=500, detail="Order total validation failed")
    return Totals(subtotal, discount, tax, shipping, total)


def _generate_order_number(db: Session) -> str:
    for _ in range(10):
        number = f"ORD-{utcnow():%Y%m%d}-{secrets.token_hex(3).upper()}"
        if db.scalar(select(Order.id).where(Order.order_number == number)) is None:
            return number
    raise HTTPException(status_code=500, detail="Could not generate a unique order number")


# ------------------------------------------------------------- stock helpers
def _reserve_ref(order_number: str, product_id: int) -> str:
    return f"{order_number}-p{product_id}-reserve"


def _cancel_ref(order_number: str, product_id: int) -> str:
    return f"{order_number}-p{product_id}-cancel"


def _compensate(order_number: str, lines: list[Line]) -> list[str]:
    """Put back stock for lines that were (or may have been) reserved."""
    failed = []
    for line in lines:
        try:
            product_client.release_stock(
                line.product_id,
                line.quantity,
                reference=f"{order_number}-p{line.product_id}-rollback",
                reverses_reference=_reserve_ref(order_number, line.product_id),
            )
        except StockOperationError as exc:
            log.critical("COMPENSATION FAILED order=%s product=%s: %s", order_number, line.product_id, exc.detail)
            failed.append(f"product {line.product_id}")
    return failed


# ------------------------------------------------------------------ creation
def create_order(db: Session, user: CurrentUser, payload: OrderCreate) -> Order:
    wanted: dict[int, int] = {}
    for item in payload.items:  # merge duplicate product lines
        wanted[item.product_id] = wanted.get(item.product_id, 0) + item.quantity

    # 1. address (must belong to the authenticated user)
    address = user_client.get_shipping_address(payload.shipping_address_id, user)
    snapshot = dict(address)
    customer = user_client.get_profile_snapshot(user)
    if customer:
        snapshot["customer"] = customer

    # 2. products: exist, active, enough stock, trusted prices
    lines: list[Line] = []
    for product_id, quantity in wanted.items():
        product = product_client.get_product(product_id)
        if not product.is_active:
            raise HTTPException(status_code=400, detail=f"Product {product_id} is not active")
        if product.stock is not None and product.stock < quantity:
            raise HTTPException(
                status_code=409,
                detail=f"Insufficient stock for product {product_id}: requested {quantity}, available {product.stock}",
            )
        lines.append(Line(product_id, product.name, product.sku, money(product.price), quantity))

    # 3. totals computed on the backend
    totals = calculate_totals(lines)
    order_number = _generate_order_number(db)

    # 4. reserve stock (idempotent references), compensate on any failure
    reserved: list[Line] = []
    try:
        for line in lines:
            reserved.append(line)  # added first: a timed-out call may still have succeeded
            product_client.reserve_stock(line.product_id, line.quantity, _reserve_ref(order_number, line.product_id))

        # 5. save order + items in one local transaction
        order = Order(
            order_number=order_number,
            user_id=user.id,  # from the JWT, never from the body
            shipping_address=snapshot,
            subtotal=totals.subtotal,
            discount_amount=totals.discount,
            shipping_charge=totals.shipping,
            tax_amount=totals.tax,
            total_amount=totals.total,
            order_status=OrderStatus.PENDING.value,
            payment_status="PENDING",
            notes=payload.notes,
            items=[
                OrderItem(
                    product_id=l.product_id,
                    product_name=l.name,
                    sku=l.sku,
                    unit_price=l.unit_price,
                    quantity=l.quantity,
                    discount_amount=l.discount,
                    total_price=l.total,
                )
                for l in lines
            ],
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        return order
    except StockOperationError as exc:
        db.rollback()
        failed = _compensate(order_number, reserved)
        detail = exc.detail + (f" (stock release pending for {', '.join(failed)})" if failed else "")
        raise HTTPException(status_code=exc.status_code, detail=detail)
    except (SQLAlchemyError, Exception) as exc:  # noqa: BLE001 - always release stock
        db.rollback()
        _compensate(order_number, reserved)
        if isinstance(exc, HTTPException):
            raise
        log.exception("Order creation failed, stock released")
        raise HTTPException(status_code=500, detail="Could not create the order; reserved stock was released")


# ----------------------------------------------------------------- retrieval
def get_order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def get_order_by_number_or_404(db: Session, order_number: str) -> Order:
    order = db.scalar(select(Order).where(Order.order_number == order_number))
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def ensure_access(order: Order, user: CurrentUser) -> Order:
    if order.user_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="You do not have access to this order")
    return order


def list_orders(
    db: Session,
    user: CurrentUser,
    page: int,
    page_size: int,
    status: Optional[OrderStatus],
    date_from: Optional[date],
    date_to: Optional[date],
    user_id: Optional[int],
) -> tuple[int, list[Order]]:
    conditions = []
    if user.is_admin:
        if user_id is not None:
            conditions.append(Order.user_id == user_id)
    else:
        conditions.append(Order.user_id == user.id)
    if status is not None:
        conditions.append(Order.order_status == status.value)
    if date_from is not None:
        conditions.append(Order.created_at >= datetime.combine(date_from, datetime.min.time()))
    if date_to is not None:
        conditions.append(Order.created_at < datetime.combine(date_to + timedelta(days=1), datetime.min.time()))

    total = db.scalar(select(func.count()).select_from(Order).where(*conditions)) or 0
    rows = db.scalars(
        select(Order)
        .where(*conditions)
        .order_by(Order.created_at.desc(), Order.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return total, list(rows)


# ------------------------------------------------------------ status / cancel
def cancel_order(db: Session, order_id: int, actor: CurrentUser) -> Order:
    """Idempotent cancellation. Stock is restored with fixed per-item references, so a retry
    (or two parallel requests) can never restore stock twice."""
    order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    ensure_access(order, actor)

    if order.order_status == OrderStatus.CANCELLED.value:
        return order  # already cancelled: nothing more to do, no stock calls

    if OrderStatus(order.order_status) not in CANCELLABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Orders in status {order.order_status} cannot be cancelled",
        )

    try:
        for item in order.items:
            product_client.release_stock(
                item.product_id,
                item.quantity,
                reference=_cancel_ref(order.order_number, item.product_id),
                reverses_reference=_reserve_ref(order.order_number, item.product_id),
            )
    except StockOperationError as exc:
        db.rollback()  # order stays cancellable; the retry re-sends the same references
        raise HTTPException(
            status_code=exc.status_code,
            detail=f"Cancellation not completed: {exc.detail}. The order was not changed; please retry.",
        )

    order.order_status = OrderStatus.CANCELLED.value
    order.updated_at = utcnow()
    db.commit()
    db.refresh(order)
    return order


def update_status(db: Session, order_id: int, new_status: OrderStatus, actor: CurrentUser) -> Order:
    order = get_order_or_404(db, order_id)
    current = OrderStatus(order.order_status)
    if new_status not in ALLOWED_TRANSITIONS[current]:
        allowed = ", ".join(s.value for s in sorted(ALLOWED_TRANSITIONS[current], key=lambda s: s.value)) or "none"
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status transition {current.value} -> {new_status.value}. Allowed: {allowed}",
        )
    if new_status == OrderStatus.CANCELLED:
        return cancel_order(db, order_id, actor)  # restores stock as well
    order.order_status = new_status.value
    order.updated_at = utcnow()
    db.commit()
    db.refresh(order)
    return order
