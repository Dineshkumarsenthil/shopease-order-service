from decimal import Decimal

from tests.conftest import make_token

BODY = {"items": [{"product_id": 1, "quantity": 2}, {"product_id": 2, "quantity": 1}], "shipping_address_id": 5,
        "notes": "evening"}


def place(client, headers, body=None):
    return client.post("/api/orders/", json=body or BODY, headers=headers)


# ---- creation -------------------------------------------------------------
def test_01_create_valid_order(client, customer, fake):
    r = place(client, customer)
    assert r.status_code == 201, r.text
    o = r.json()
    assert o["order_status"] == "PENDING" and o["payment_status"] == "PENDING"
    assert o["user_id"] == 2 and o["order_number"].startswith("ORD-")
    assert len(o["items"]) == 2
    # subtotal 2*1999.99 + 25.50 = 4025.48 ; no discount (<5000) ; tax 18% = 724.59 ; free shipping (>=1000)
    assert Decimal(o["subtotal"]) == Decimal("4025.48")
    assert Decimal(o["discount_amount"]) == Decimal("0.00")
    assert Decimal(o["tax_amount"]) == Decimal("724.59")
    assert Decimal(o["shipping_charge"]) == Decimal("0.00")
    assert Decimal(o["total_amount"]) == Decimal("4750.07")
    assert o["shipping_address"]["city"] == "Madurai"
    assert fake.stock(1) == 8 and fake.stock(2) == 99


def test_discount_and_item_totals_reconcile(client, customer):
    r = place(client, customer, {"items": [{"product_id": 1, "quantity": 3}, {"product_id": 2, "quantity": 7}],
                                 "shipping_address_id": 5})
    o = r.json()
    assert Decimal(o["discount_amount"]) > 0
    assert sum(Decimal(i["total_price"]) for i in o["items"]) == Decimal(o["subtotal"]) - Decimal(o["discount_amount"])
    assert sum(Decimal(i["discount_amount"]) for i in o["items"]) == Decimal(o["discount_amount"])
    assert Decimal(o["total_amount"]) == (Decimal(o["subtotal"]) - Decimal(o["discount_amount"])
                                          + Decimal(o["tax_amount"]) + Decimal(o["shipping_charge"]))


def test_client_cannot_set_price_user_or_total(client, customer):
    body = {**BODY, "user_id": 99, "total_amount": "1.00", "items": [{"product_id": 2, "quantity": 1, "unit_price": "0.01"}]}
    o = place(client, customer, body).json()
    assert o["user_id"] == 2
    assert Decimal(o["items"][0]["unit_price"]) == Decimal("25.50")


def test_02_invalid_product(client, customer):
    r = place(client, customer, {"items": [{"product_id": 999, "quantity": 1}], "shipping_address_id": 5})
    assert r.status_code == 404


def test_03_inactive_product(client, customer, fake):
    r = place(client, customer, {"items": [{"product_id": 3, "quantity": 1}], "shipping_address_id": 5})
    assert r.status_code == 400 and "not active" in r.json()["detail"]


def test_04_insufficient_stock_and_nothing_saved(client, customer, fake):
    r = place(client, customer, {"items": [{"product_id": 2, "quantity": 1}, {"product_id": 4, "quantity": 50}],
                                 "shipping_address_id": 5})
    assert r.status_code == 409
    assert fake.stock(2) == 100 and fake.stock(4) == 3
    assert client.get("/api/orders/", headers=customer).json()["total"] == 0


def test_05_invalid_address_and_other_users_address(client, customer):
    assert place(client, customer, {**BODY, "shipping_address_id": 404}).status_code == 404
    assert place(client, customer, {**BODY, "shipping_address_id": 6}).status_code == 404  # belongs to user 3


def test_06_no_token_and_07_bad_token(client):
    assert client.post("/api/orders/", json=BODY).status_code == 401
    assert client.post("/api/orders/", json=BODY, headers={"Authorization": "Bearer not.a.token"}).status_code == 401
    wrong = {"Authorization": f"Bearer {make_token(2, secret='other-secret')}"}
    assert client.post("/api/orders/", json=BODY, headers=wrong).status_code == 401
    expired = {"Authorization": f"Bearer {make_token(2, expired=True)}"}
    assert client.post("/api/orders/", json=BODY, headers=expired).status_code == 401


def test_validation_errors(client, customer):
    assert place(client, customer, {"items": [], "shipping_address_id": 5}).status_code == 422
    assert place(client, customer, {"items": [{"product_id": 1, "quantity": 0}], "shipping_address_id": 5}).status_code == 422


def test_duplicate_lines_are_merged(client, customer, fake):
    r = place(client, customer, {"items": [{"product_id": 2, "quantity": 1}, {"product_id": 2, "quantity": 2}],
                                 "shipping_address_id": 5})
    assert r.status_code == 201 and len(r.json()["items"]) == 1 and r.json()["items"][0]["quantity"] == 3
    assert fake.stock(2) == 97


# ---- retrieval -------------------------------------------------------------
def test_08_list_own_orders_and_09_other_users_order(client, customer, other_customer, admin):
    oid = place(client, customer).json()["id"]
    mine = client.get("/api/orders/", headers=customer).json()
    assert mine["total"] == 1 and mine["items"][0]["id"] == oid
    assert client.get("/api/orders/", headers=other_customer).json()["total"] == 0
    assert client.get(f"/api/orders/{oid}", headers=other_customer).status_code == 403
    assert client.get(f"/api/orders/{oid}/items", headers=other_customer).status_code == 403
    assert client.get(f"/api/orders/{oid}", headers=admin).status_code == 200
    assert client.get("/api/orders/9999", headers=customer).status_code == 404


def test_10_get_by_number(client, customer, other_customer):
    o = place(client, customer).json()
    r = client.get(f"/api/orders/number/{o['order_number']}", headers=customer)
    assert r.status_code == 200 and r.json()["id"] == o["id"]
    assert client.get(f"/api/orders/number/{o['order_number']}", headers=other_customer).status_code == 403
    assert client.get("/api/orders/number/ORD-NOPE", headers=customer).status_code == 404
    items = client.get(f"/api/orders/{o['id']}/items", headers=customer).json()
    assert len(items) == 2 and items[0]["sku"] == "LAP-001"


def test_11_pagination_and_filters(client, customer, admin):
    ids = [place(client, customer, {"items": [{"product_id": 2, "quantity": 1}], "shipping_address_id": 5}).json()["id"]
           for _ in range(5)]
    page1 = client.get("/api/orders/?page=1&page_size=2", headers=customer).json()
    page3 = client.get("/api/orders/?page=3&page_size=2", headers=customer).json()
    assert page1["total"] == 5 and len(page1["items"]) == 2 and len(page3["items"]) == 1
    client.patch(f"/api/orders/{ids[0]}/status", json={"order_status": "CONFIRMED"}, headers=admin)
    assert client.get("/api/orders/?status=CONFIRMED", headers=customer).json()["total"] == 1
    assert client.get("/api/orders/?status=PENDING", headers=customer).json()["total"] == 4
    assert client.get("/api/orders/?date_from=2999-01-01", headers=customer).json()["total"] == 0
    assert client.get("/api/orders/?date_from=2000-01-01&date_to=2999-01-01", headers=customer).json()["total"] == 5
    assert client.get("/api/orders/?status=BOGUS", headers=customer).status_code == 422


# ---- status ---------------------------------------------------------------
def test_12_13_14_status_updates(client, customer, admin):
    oid = place(client, customer).json()["id"]
    path = f"/api/orders/{oid}/status"
    assert client.patch(path, json={"order_status": "CONFIRMED"}, headers=customer).status_code == 403  # 13
    r = client.patch(path, json={"order_status": "CONFIRMED"}, headers=admin)  # 12
    assert r.status_code == 200 and r.json()["order_status"] == "CONFIRMED"
    r = client.patch(path, json={"order_status": "DELIVERED"}, headers=admin)  # 14 invalid jump
    assert r.status_code == 400 and "Invalid status transition" in r.json()["detail"]
    assert client.patch(path, json={"order_status": "CONFIRMED"}, headers=admin).status_code == 400
    assert client.patch(path, json={"order_status": "NOPE"}, headers=admin).status_code == 422
    assert client.patch(path, json={"order_status": "CONFIRMED"}).status_code == 401
    for s in ("PROCESSING", "SHIPPED", "DELIVERED"):
        assert client.patch(path, json={"order_status": s}, headers=admin).status_code == 200


# ---- cancellation ----------------------------------------------------------
def test_15_17_18_cancel_restores_stock_once(client, customer, fake):
    oid = place(client, customer).json()["id"]
    assert fake.stock(1) == 8
    r = client.post(f"/api/orders/{oid}/cancel", headers=customer)
    assert r.status_code == 200 and r.json()["order_status"] == "CANCELLED"
    assert fake.stock(1) == 10 and fake.stock(2) == 100  # 17 restored
    again = client.post(f"/api/orders/{oid}/cancel", headers=customer)  # 18 repeat
    assert again.status_code == 200 and again.json()["order_status"] == "CANCELLED"
    assert fake.stock(1) == 10 and fake.stock(2) == 100


def test_cancel_other_users_order_forbidden(client, customer, other_customer, fake):
    oid = place(client, customer).json()["id"]
    assert client.post(f"/api/orders/{oid}/cancel", headers=other_customer).status_code == 403
    assert fake.stock(1) == 8


def test_16_cannot_cancel_shipped_or_delivered(client, customer, admin, fake):
    oid = place(client, customer).json()["id"]
    for s in ("CONFIRMED", "PROCESSING", "SHIPPED"):
        client.patch(f"/api/orders/{oid}/status", json={"order_status": s}, headers=admin)
    r = client.post(f"/api/orders/{oid}/cancel", headers=customer)
    assert r.status_code == 400
    client.patch(f"/api/orders/{oid}/status", json={"order_status": "DELIVERED"}, headers=admin)
    assert client.post(f"/api/orders/{oid}/cancel", headers=customer).status_code == 400
    assert fake.stock(1) == 8


def test_admin_cancel_via_status_restores_stock(client, customer, admin, fake):
    oid = place(client, customer).json()["id"]
    r = client.patch(f"/api/orders/{oid}/status", json={"order_status": "CANCELLED"}, headers=admin)
    assert r.status_code == 200 and fake.stock(1) == 10
    assert client.post(f"/api/orders/{oid}/cancel", headers=customer).status_code == 200
    assert fake.stock(1) == 10


def test_cancel_retry_after_catalog_failure_does_not_double_restore(client, customer, fake):
    oid = place(client, customer).json()["id"]
    fake.catalog_down = True
    r = client.post(f"/api/orders/{oid}/cancel", headers=customer)
    assert r.status_code == 503
    fake.catalog_down = False
    assert client.get(f"/api/orders/{oid}", headers=customer).json()["order_status"] == "PENDING"
    assert client.post(f"/api/orders/{oid}/cancel", headers=customer).status_code == 200
    assert client.post(f"/api/orders/{oid}/cancel", headers=customer).status_code == 200
    assert fake.stock(1) == 10 and fake.stock(2) == 100


# ---- failures / consistency --------------------------------------------------
def test_19_20_catalog_down_creates_nothing(client, customer, fake):
    fake.catalog_down = True
    r = place(client, customer)
    assert r.status_code == 503
    fake.catalog_down = False
    assert client.get("/api/orders/", headers=customer).json()["total"] == 0
    assert fake.stock(1) == 10 and fake.stock(2) == 100



def test_user_service_unavailable_returns_503(client, customer, fake):
    fake.user_service_down = True
    assert place(client, customer).status_code == 503
    assert fake.stock(1) == 10


def test_partial_reservation_failure_is_compensated(client, customer, fake):
    fake.fail_stock_out_for = {2}  # product 1 reserved first, then product 2 fails with 500
    r = place(client, customer)
    assert r.status_code == 503
    assert fake.stock(1) == 10 and fake.stock(2) == 100
    assert client.get("/api/orders/", headers=customer).json()["total"] == 0


def test_database_failure_after_reservation_releases_stock(client, customer, fake, monkeypatch):
    from sqlalchemy.orm import Session

    def boom(self):
        raise RuntimeError("db down")

    monkeypatch.setattr(Session, "commit", boom)
    r = place(client, customer)
    assert r.status_code == 500
    monkeypatch.undo()
    assert fake.stock(1) == 10 and fake.stock(2) == 100


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"
