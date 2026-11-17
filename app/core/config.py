from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "mysql+pymysql://order_user:order_pass@localhost:3309/shopease_order_db"

    # JWT (must match the Authentication Service)
    JWT_SECRET_KEY: str = "change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_USER_ID_CLAIM: str = "sub"
    JWT_ROLE_CLAIM: str = "role"
    ADMIN_ROLES: str = "admin,administrator"
    SERVICE_ACCOUNT_SUB: str = "order-service"

    # Other services
    USER_SERVICE_URL: str = "http://localhost:8002"
    PRODUCT_SERVICE_URL: str = "http://localhost:8003"
    HTTP_TIMEOUT_SECONDS: float = 5.0

    USER_ADDRESS_PATH: str = "/api/addresses/{address_id}"
    USER_PROFILE_PATH: str = "/api/users/me"
    PRODUCT_DETAIL_PATH: str = "/api/products/{product_id}"
    STOCK_OUT_PATH: str = "/api/inventory/{product_id}/stock-out"
    STOCK_IN_PATH: str = "/api/inventory/{product_id}/stock-in"

    # Pricing rules
    TAX_RATE: Decimal = Decimal("0.18")
    SHIPPING_CHARGE: Decimal = Decimal("50.00")
    FREE_SHIPPING_THRESHOLD: Decimal = Decimal("1000.00")
    DISCOUNT_MIN_SUBTOTAL: Decimal = Decimal("5000.00")
    DISCOUNT_PERCENT: Decimal = Decimal("10")

    @property
    def admin_roles(self) -> set[str]:
        return {r.strip().lower() for r in self.ADMIN_ROLES.split(",") if r.strip()}


settings = Settings()
