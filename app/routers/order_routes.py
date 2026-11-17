from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.security import CurrentUser, get_current_user, require_admin
from app.database.connection import get_db
from app.models.order_model import OrderStatus
from app.schemas.order_schema import OrderCreate, OrderListOut, OrderOut, OrderStatusUpdate
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["Orders"])


@router.post("/", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(
    payload: OrderCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return order_service.create_order(db, user, payload)


@router.get("/", response_model=OrderListOut)
def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    status_filter: Optional[OrderStatus] = Query(None, alias="status"),
    date_from: Optional[date] = Query(None, description="Inclusive, YYYY-MM-DD"),
    date_to: Optional[date] = Query(None, description="Inclusive, YYYY-MM-DD"),
    user_id: Optional[int] = Query(None, description="Admins only: filter by customer"),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    total, orders = order_service.list_orders(db, user, page, page_size, status_filter, date_from, date_to, user_id)
    return OrderListOut(total=total, page=page, page_size=page_size, items=orders)


@router.get("/number/{order_number}", response_model=OrderOut)
def get_order_by_number(
    order_number: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    order = order_service.get_order_by_number_or_404(db, order_number)
    return order_service.ensure_access(order, user)


@router.get("/{order_id}", response_model=OrderOut)
def get_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    order = order_service.get_order_or_404(db, order_id)
    return order_service.ensure_access(order, user)


@router.patch("/{order_id}/status", response_model=OrderOut)
def update_order_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: Session = Depends(get_db),
    admin: CurrentUser = Depends(require_admin),
):
    return order_service.update_status(db, order_id, payload.order_status, admin)


@router.post("/{order_id}/cancel", response_model=OrderOut)
def cancel_order(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return order_service.cancel_order(db, order_id, user)
