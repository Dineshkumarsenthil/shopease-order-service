import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET_KEY"] = "test-secret"
os.environ["USER_SERVICE_URL"] = "http://user-service"
os.environ["PRODUCT_SERVICE_URL"] = "http://catalog-service"

import json
from datetime import datetime, timedelta, timezone

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from app.database.connection import Base, engine
from app.main import app
from app.services import http_client


class FakeServices:
    """In-memory stand-ins for the User Service and Product Catalog Service,
    including idempotent stock references like the patched Catalog."""

    def __init__(self):
        self.catalog_down = False
        self.user_service_down = False
        self.products = {
            1: {"id": 1, "name": "Laptop", "sku": "LAP-001", "price": "1999.99", "is_active": True, "stock_quantity": 10},
            2: {"id": 2, "name": "Mouse", "sku": "MOU-001", "price": "25.50", "is_active": True, "stock_quantity": 100},
            3: {"id": 3, "name": "Old Phone", "sku": "PHN-OLD", "price": "99.00", "is_active": False, "stock_quantity": 5},
            4: {"id": 4, "name": "Keyboard", "sku": "KEY-001", "price": "60.00", "is_active": True, "stock_quantity": 3},
        }
        self.addresses = {
            5: {"id": 5, "user_id": 2, "line1": "12 Main St", "city": "Madurai", "state": "TN", "pincode": "625001"},
            6: {"id": 6, "user_id": 3, "line1": "99 Other St", "city": "Chennai", "state": "TN", "pincode": "600001"},
        }
        self.movements = {}  # reference -> (product_id, delta)
        self.fail_stock_out_for = set()  # product ids whose stock-out returns 500

    def stock(self, pid):
        return self.products[pid]["stock_quantity"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        token = request.headers.get("authorization", "").replace("Bearer ", "")
        try:
            claims = jwt.decode(token, "test-secret", algorithms=["HS256"], options={"verify_sub": False})
        except jwt.PyJWTError:
            return httpx.Response(401, json={"detail": "bad token"})

        if host == "user-service":
            if self.user_service_down:
                raise httpx.ConnectError("user service down")
            if path.startswith("/api/addresses/"):
                addr = self.addresses.get(int(path.rsplit("/", 1)[1]))
                if addr is None:
                    return httpx.Response(404, json={"detail": "not found"})
                if str(addr["user_id"]) != str(claims["sub"]):
                    return httpx.Response(403, json={"detail": "forbidden"})
                return httpx.Response(200, json=addr)
            if path == "/api/users/me":
                return httpx.Response(200, json={"full_name": "Test User", "email": "t@example.com"})

        if host == "catalog-service":
            if self.catalog_down:
                raise httpx.ConnectError("catalog down")
            parts = path.strip("/").split("/")
            if parts[:2] == ["api", "products"]:
                p = self.products.get(int(parts[2]))
                return httpx.Response(200, json=p) if p else httpx.Response(404, json={"detail": "nf"})
            if parts[:2] == ["api", "inventory"]:
                if claims.get("role") != "admin":
                    return httpx.Response(403, json={"detail": "admin only"})
                pid, action = int(parts[2]), parts[3]
                body = json.loads(request.content)
                ref, qty = body["reference"], body["quantity"]
                product = self.products[pid]
                if action == "stock-out":
                    if pid in self.fail_stock_out_for:
                        return httpx.Response(500, json={"detail": "boom"})
                    if ref in self.movements:
                        return httpx.Response(200, json={"idempotent": True})
                    if product["stock_quantity"] < qty:
                        return httpx.Response(400, json={"detail": "Insufficient stock"})
                    product["stock_quantity"] -= qty
                    self.movements[ref] = (pid, -qty)
                    return httpx.Response(200, json={"ok": True})
                if action == "stock-in":
                    rev = body.get("reverses_reference")
                    if ref in self.movements or (rev and rev not in self.movements):
                        return httpx.Response(200, json={"idempotent": True})
                    product["stock_quantity"] += qty
                    self.movements[ref] = (pid, qty)
                    return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "unknown route in fake"})


def make_token(user_id: int, role: str = "customer", expired: bool = False, secret: str = "test-secret") -> str:
    exp = datetime.now(timezone.utc) + timedelta(hours=-1 if expired else 1)
    return jwt.encode({"sub": str(user_id), "role": role, "exp": exp}, secret, algorithm="HS256")


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def fake(monkeypatch):
    services = FakeServices()
    monkeypatch.setattr(http_client, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(services.handler)))
    return services


@pytest.fixture
def client(fake):
    return TestClient(app)


@pytest.fixture
def customer():
    return {"Authorization": f"Bearer {make_token(2)}"}


@pytest.fixture
def other_customer():
    return {"Authorization": f"Bearer {make_token(3)}"}


@pytest.fixture
def admin():
    return {"Authorization": f"Bearer {make_token(1, 'admin')}"}
