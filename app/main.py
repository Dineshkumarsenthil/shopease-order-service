import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from app.routers import order_item_routes, order_routes

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="ShopEase Order Management Service",
    description="Creates and manages customer orders; talks to the Auth, User and Product Catalog services over REST.",
    version="1.0.0",
)

app.include_router(order_routes.router)
app.include_router(order_item_routes.router)


@app.exception_handler(SQLAlchemyError)
async def database_error_handler(request: Request, exc: SQLAlchemyError):
    logging.getLogger(__name__).exception("Database error")
    return JSONResponse(status_code=500, content={"detail": "Database error"})


@app.get("/health", tags=["Health"])
def health():
    return {"status": "ok", "service": "order-service"}
