# ShopEase Order Service

A FastAPI microservice that manages customer orders: create an order, view orders, update status and cancel.
It has its own MySQL database and talks to the Auth, User and Product Catalog services through REST APIs.

**Tech stack:** Python 3.11, FastAPI, SQLAlchemy, Alembic, MySQL 8, JWT, HTTPX, Docker Compose

## Folder structure

```
shopease-order-service/
├── app/
│   ├── main.py            # app entry point and /health
│   ├── routers/           # order and order item endpoints
│   ├── models/            # database models
│   ├── schemas/           # request and response models
│   ├── services/          # order logic and clients for User and Product services
│   ├── core/              # settings and JWT security
│   └── database/          # database connection
├── alembic/               # database migrations
├── tests/                 # automated tests
├── docs/                  # extra notes
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example           # sample environment variables
```

## How it works

- **Login:** this service does not create tokens. It checks the JWT from the Auth Service.
- **Users:** customers see only their own orders. Admins see all orders and can change status.
- **Address:** the shipping address is fetched from the User Service when an order is placed.
- **Products:** product name, SKU and price are read from the Product Catalog. Stock is reduced when an order is created and restored when it is cancelled.
- **Safety:** if anything fails while creating an order, the reserved stock is given back and no order is saved.

## Database

- `orders`: order number, user, shipping address, subtotal, discount, shipping, tax, total, order status, payment status
- `order_items`: product, name, SKU, unit price, quantity, total price

One order has many items.

## API endpoints

| Method | Endpoint | Who can use it | What it does |
|---|---|---|---|
| POST | `/api/orders/` | customer, admin | Create an order |
| GET | `/api/orders/` | customer, admin | List orders (with page and filters) |
| GET | `/api/orders/{order_id}` | owner, admin | Get order details |
| GET | `/api/orders/number/{order_number}` | owner, admin | Get order by order number |
| GET | `/api/orders/{order_id}/items` | owner, admin | Get order items |
| PATCH | `/api/orders/{order_id}/status` | admin | Change order status |
| POST | `/api/orders/{order_id}/cancel` | owner, admin | Cancel an order |
| GET | `/health` | public | Health check |

## Order status flow

```
PENDING -> CONFIRMED -> PROCESSING -> SHIPPED -> DELIVERED
   |           |
   +-----------+--> CANCELLED
```

An order can only be cancelled while it is PENDING or CONFIRMED. Any other change returns a 400 error.

## Price calculation

- Subtotal = sum of (price x quantity)
- Discount = 10% when the subtotal is 5000 or more
- Tax = 18% of (subtotal - discount)
- Shipping = 50, or free when (subtotal - discount) is 1000 or more
- **Total = subtotal - discount + tax + shipping**

These values can be changed in the `.env` file.

## Setup

1. Copy the sample file and edit it:
   ```bash
   cp .env.example .env
   ```
2. Set `JWT_SECRET_KEY` to the **same value** used by the Auth Service.
3. Check that the User and Product service URLs and paths in `.env` are correct.

## Run with Docker

Start the Auth, User and Product services first, then run:

```bash
docker compose up --build -d
```

Swagger docs: http://localhost:8004/docs

Stop the service:

```bash
docker compose down        # keep data
docker compose down -v     # delete data
```

## Run locally

```bash
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8004
```

## Run tests

```bash
python -m pytest -q
```

The tests use SQLite and fake User and Product services, so MySQL is not needed.

## Migrations

```bash
alembic upgrade head      # apply
alembic downgrade base    # roll back
alembic current           # show current version
```

## Error codes

| Code | Meaning |
|---|---|
| 400 | Invalid action (for example, inactive product or wrong status change) |
| 401 | Missing or invalid token |
| 403 | You do not have permission |
| 404 | Product or address not found |
| 409 | Not enough stock |
| 503 | Another service is down |
