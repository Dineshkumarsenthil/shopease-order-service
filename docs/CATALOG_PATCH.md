# Product Catalog patch: idempotent stock changes

The Order Service reserves and restores stock through the Product Catalog's existing stock-out and
stock-in endpoints. For cancellation and failure recovery to be safe, the Catalog must make those two
calls **idempotent**. This file describes the small change. It was written without access to your
Catalog source, so adapt the names to your code.

## 1. What the Order Service sends

`POST /api/inventory/{product_id}/stock-out`
```json
{ "quantity": 2, "reference": "ORD-20261116-A1B2C3-p1-reserve", "reason": "Order reservation" }
```

`POST /api/inventory/{product_id}/stock-in`
```json
{ "quantity": 2, "reference": "ORD-20261116-A1B2C3-p1-cancel",
  "reverses_reference": "ORD-20261116-A1B2C3-p1-reserve", "reason": "Order stock release" }
```

The calls carry an `Authorization: Bearer <token>` header. The token is signed with the shared
`JWT_SECRET_KEY` and carries `role: admin` and `sub: order-service`. The Catalog's admin check must accept it
without looking `sub` up in a users table (or set `SERVICE_ACCOUNT_SUB` in the Order Service to a real admin id).

## 2. Rules to implement

* `reference` is optional on both endpoints, so existing callers keep working.
* If a request arrives with a `reference` the Catalog has already processed, return the earlier result with
  HTTP 200 and **do not change stock**.
* On stock-in, if `reverses_reference` is given and no stock-out with that reference exists, return 200 and
  change nothing. This makes cleanup after a timed-out reservation safe.
* Insufficient stock on stock-out should return 400 (or 409/422); the Order Service maps all of them to 409.

## 3. Migration 0002 (new table, nothing existing is touched)

```python
def upgrade():
    op.create_table(
        "stock_references",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reference", sa.String(100), nullable=False, unique=True),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("delta", sa.Integer(), nullable=False),   # negative = stock-out, positive = stock-in
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

def downgrade():
    op.drop_table("stock_references")
```

## 4. Service logic (sketch)

```python
def apply_stock_change(db, product, delta, reference=None, reverses_reference=None):
    if reference:
        existing = db.scalar(select(StockReference).where(StockReference.reference == reference))
        if existing:
            return existing_result              # idempotent: same reference, no second change
    if reverses_reference and not db.scalar(
            select(StockReference).where(StockReference.reference == reverses_reference)):
        return no_op_result                     # nothing was reserved, nothing to restore

    # ... your existing stock update + inventory history row here ...

    if reference:
        db.add(StockReference(reference=reference, product_id=product.id, delta=delta))
    db.commit()   # stock change and reference row commit together; the UNIQUE constraint
                  # stops two parallel requests with the same reference from both applying
```

Catch `IntegrityError` on the commit, roll back, and return the earlier result (that is the
parallel-request case).

## 5. Product detail response

The Order Service reads the product with `GET /api/products/{id}` and looks for these fields (first match wins):
price: `price`/`unit_price`; stock: `stock_quantity`/`quantity`/`stock`/`available_stock`; active:
`is_active`/`active`, or `status == "ACTIVE"`; also `name` and `sku`. If your names differ, adjust
`_parse_product` in `app/services/product_client.py`. If the product response has no stock field, the pre-check is
skipped and the stock-out call alone decides.
