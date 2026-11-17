# ShopEase Order Management Service

Independent FastAPI microservice that handles the full customer order lifecycle: creation, pricing, order
items, status management, history and cancellation. It has its own MySQL database (`shopease_order_db`) and
talks to the Authentication, User Management and Product Catalog services **only through REST APIs**.

**Stack:** Python 3.11, FastAPI, SQLAlchemy 2, Alembic, MySQL 8, JWT (PyJWT), HTTPX, Docker Compose.

## Folder structure

```
shopease-order-service/
├── app/
│   ├── main.py                    # FastAPI app, /health
│   ├── routers/order_routes.py    # create, list, get, by number, status, cancel
│   ├── routers/order_item_routes.py   # GET /api/orders/{id}/items
│   ├── models/                    # Order, OrderItem (SQLAlchemy)
│   ├── schemas/                   # Pydantic request/response models
│   ├── services/order_service.py  # pricing, creation + compensation, cancel, status rules
│   ├── services/user_client.py    # HTTPX client for the User Service
│   ├── services/product_client.py # HTTPX client for the Product Catalog (reads + stock)
│   ├── core/config.py, security.py
│   └── database/connection.py
├── alembic/                       # migration 0001 creates both tables
├── tests/                         # pytest suite with fake User/Catalog services
├── docs/CATALOG_PATCH.md          # idempotent stock change needed in the Product Catalog
├── postman_collection.json        # import into Postman or Bruno
├── Dockerfile, docker-compose.yml, requirements.txt, .env, .dockerignore
```

## Database schema

`orders`: id, order_number (UNIQUE), user_id, shipping_address (JSON snapshot), subtotal, discount_amount,
shipping_charge, tax_amount, total_amount (all `DECIMAL(12,2)`), order_status, payment_status, notes,
created_at, updated_at.

`order_items`: id, order_id (FK, ON DELETE CASCADE), product_id, product_name, sku, unit_price (snapshots),
quantity, discount_amount, total_price, created_at.

One order has many items. There are no local user or product tables; `product_id` and `user_id` are plain references.

## Authentication integration

The service never issues tokens. It validates the `Authorization: Bearer <JWT>` header with the Auth Service's
secret (`JWT_SECRET_KEY`, `JWT_ALGORITHM`) and reads the user id (`JWT_USER_ID_CLAIM`, default `sub`) and role
(`JWT_ROLE_CLAIM`, default `role`). Roles listed in `ADMIN_ROLES` are administrators.

* Missing/invalid/expired token: **401**. Valid token without permission: **403**.
* `user_id` on an order always comes from the token. Any `user_id` or price in the request body is ignored.
* Customers see only their own orders; administrators see all. Only administrators can use `PATCH .../status`.

## User Service integration

On order creation the address is fetched with the **customer's own token** (`USER_ADDRESS_PATH`), so the User
Service refuses another user's address; the response `user_id` is checked too. A wrong or foreign address returns
404. The address (plus a best-effort name/email/phone from `USER_PROFILE_PATH`) is saved as the order's
`shipping_address` snapshot. If the User Service is down: **503**.

## Product Catalog integration

Product details (`PRODUCT_DETAIL_PATH`) and stock changes (`STOCK_OUT_PATH`, `STOCK_IN_PATH`) use a short-lived
admin-role JWT that the Order Service signs itself with the shared secret. Customer tokens cannot call admin-only
inventory endpoints. Prices always come from the Catalog. Unknown product: 404, inactive: 400, not enough
stock: 409, Catalog down: 503. Product name, SKU and price are copied into each order item.

The Catalog must support idempotent stock references; see `docs/CATALOG_PATCH.md`.

## API endpoints

| Method | Endpoint | Access | Purpose |
|---|---|---|---|
| POST | `/api/orders/` | customer/admin | Create order (201) |
| GET | `/api/orders/` | customer/admin | List own orders (admin: all). Query: `page`, `page_size`, `status`, `date_from`, `date_to`, `user_id` (admin) |
| GET | `/api/orders/{order_id}` | owner/admin | Order details with items |
| GET | `/api/orders/number/{order_number}` | owner/admin | Order by number |
| GET | `/api/orders/{order_id}/items` | owner/admin | Order items |
| PATCH | `/api/orders/{order_id}/status` | admin | Change status |
| POST | `/api/orders/{order_id}/cancel` | owner/admin | Cancel order |
| GET | `/health` | public | Health check |

Money values are returned as strings (for example `"4750.07"`) so no precision is lost.

## Order status workflow

```
PENDING ──► CONFIRMED ──► PROCESSING ──► SHIPPED ──► DELIVERED
   │            │
   └────────────┴──► CANCELLED
```

| From | Allowed next |
|---|---|
| PENDING | CONFIRMED, CANCELLED |
| CONFIRMED | PROCESSING, CANCELLED |
| PROCESSING | SHIPPED |
| SHIPPED | DELIVERED |
| DELIVERED, CANCELLED | none |

Anything else returns 400. Setting CANCELLED through the status endpoint runs the same cancellation workflow
(including stock restore).

## Order calculation rules

All maths uses `Decimal`, rounded to 2 places (half up). Rules are configurable in `.env`.

* Item amount = unit price (from the Catalog) x quantity
* Subtotal = sum of item amounts
* Discount = `DISCOUNT_PERCENT` % of the subtotal when subtotal >= `DISCOUNT_MIN_SUBTOTAL` (defaults 10% from 5000), split across items so item discounts add up exactly
* Tax = `TAX_RATE` x (subtotal - discount) (default 18%)
* Shipping = `SHIPPING_CHARGE` (default 50.00), or 0 when (subtotal - discount) >= `FREE_SHIPPING_THRESHOLD` (default 1000)
* **Total = Subtotal - Discount + Tax + Shipping**

Before saving, the service checks that the item totals add up to subtotal minus discount.

## Inventory reservation and cancellation workflow

The two services have separate databases, so one transaction cannot cover both. The Order Service uses
reserve-then-compensate with idempotent references:

1. Validate address and products, compute totals, generate the order number.
2. For each item, call stock-out with reference `<order_number>-p<product_id>-reserve`.
3. Save the order and items in one local transaction.
4. If **any** step fails (stock-out error, Catalog outage, database error), call stock-in with reference
   `<order_number>-p<product_id>-rollback` and `reverses_reference` pointing at the reserve reference, for every item
   already reserved, including the one whose call failed (it may have succeeded before a timeout). No order row is saved.
5. If compensation itself fails, the error is logged at CRITICAL level with the references so it can be reconciled.

**Cancellation** is allowed only from PENDING or CONFIRMED. Each item is restored with the fixed reference
`<order_number>-p<product_id>-cancel`; only after all restores succeed is the order marked CANCELLED. If the Catalog is down
the order stays unchanged (503) and a retry re-sends the same references. Repeating a cancel on a cancelled order returns
200 with no stock calls, and because the Catalog ignores repeated references, stock can never be restored twice.

## Environment variables

See `.env` / `.env.example`: `DATABASE_URL`, `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `JWT_USER_ID_CLAIM`, `JWT_ROLE_CLAIM`,
`ADMIN_ROLES`, `SERVICE_ACCOUNT_SUB`, `USER_SERVICE_URL`, `PRODUCT_SERVICE_URL`, `HTTP_TIMEOUT_SECONDS`, the five
`*_PATH` settings for the other services' endpoints, and the pricing rules.
**Set `JWT_SECRET_KEY` to the same value as the Auth Service and check the paths match your User and Catalog services.**

## Alembic migrations

```bash
alembic upgrade head      # apply
alembic downgrade base    # roll back
alembic current           # show version
```

## Run with Docker

```bash
docker compose up --build -d        # MySQL (health-checked) + service; migrations run before startup
docker compose logs -f order-service
# Swagger UI: http://localhost:8004/docs
docker compose down                 # keep data (named volume order_db_data)
docker compose down -v              # delete data
```

The other services run in their own Compose projects, so the container reaches them at
`host.docker.internal:8002` (User) and `:8003` (Catalog). Start those first.

## Run locally / tests

```bash
pip install -r requirements.txt
python -m pytest -q       # 24 tests, uses SQLite and fake User/Catalog services, no MySQL needed
uvicorn app.main:app --reload --port 8004
```

## API testing results

Automated: `pytest` covers all 21 mandatory scenarios (valid order, invalid product, inactive product, insufficient stock,
invalid address, missing/invalid token, own orders, another user's order, by number, pagination and filters, admin status
update, customer status attempt, invalid transition, cancel, cancel shipped/delivered, stock restored, repeat cancel,
Catalog outage, consistency after failure) plus pricing and partial-failure compensation.

Manual (fill in after running in Swagger/Bruno against your real services; import `postman_collection.json`):

| # | Scenario | Expected | Result |
|---|---|---|---|
| 1 | Create order, valid products | 201, PENDING, stock reduced | |
| 2 | Invalid product id | 404 | |
| 3 | Inactive product | 400 | |
| 4 | Insufficient stock | 409, stock unchanged | |
| 5 | Invalid shipping address | 404 | |
| 6 | No token | 401 | |
| 7 | Invalid token | 401 | |
| 8 | List my orders | 200 | |
| 9 | Another user's order | 403 | |
| 10 | Get by order number | 200 | |
| 11 | Pagination and filters | 200 | |
| 12 | Admin updates status | 200 | |
| 13 | Customer updates status | 403 | |
| 14 | Invalid transition | 400 | |
| 15 | Cancel eligible order | 200, CANCELLED | |
| 16 | Cancel shipped/delivered | 400 | |
| 17 | Stock restored after cancel | stock back | |
| 18 | Repeat cancel | 200, stock not restored again | |
| 19 | Catalog down | 503 | |
| 20 | Consistency after failure | no order row, stock unchanged | |
