from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import CurrentUser, get_current_user
from app.database.connection import get_db
from app.schemas.order_item_schema import OrderItemOut
from app.services import order_service

router = APIRouter(prefix="/api/orders", tags=["Order Items"])


@router.get("/{order_id}/items", response_model=list[OrderItemOut])
def get_order_items(
    order_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    order = order_service.get_order_or_404(db, order_id)
    order_service.ensure_access(order, user)
    return order.items
