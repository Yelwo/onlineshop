# Pure Python only — no Enum, no Pydantic, no Decimal (all break Graftcode codegen).
# Money is in integer minor units (grosze). Helpers live as private static methods
# on PricingService so they are not exposed as public graft functions.

import json


class Product:
    def __init__(self, id: str, name: str, price_cents: int, stock: int):
        self.id = id
        self.name = name
        self.price_cents = price_cents
        self.stock = stock


class PricingService:
    # Customer types as plain string constants (no Enum).
    REGULAR = "regular"
    PREMIUM = "premium"

    # Discount rules, expressed as whole-percent points and applied additively.
    _PREMIUM_DISCOUNT = 10
    _BULK_DISCOUNT = 5
    _BULK_THRESHOLD = 10

    # In-memory catalog — sample project, no external DB. Prices in minor units.
    _CATALOG: dict[str, Product] = {
        "laptop": Product("laptop", "Laptop", 500000, 5),
        "mouse": Product("mouse", "Mouse", 15000, 100),
        "keyboard": Product("keyboard", "Keyboard", 30000, 50),
    }

    @staticmethod
    def calculate_price(
        product_id: str, quantity: int, customer_type: str
    ) -> str:
        product = PricingService._get_product(product_id)
        if product is None:
            raise Exception(f"Unknown product: {product_id!r}")
        return PricingService._price_for(product, quantity, customer_type)

    @staticmethod
    def is_available(product_id: str, quantity: int) -> bool:
        product = PricingService._get_product(product_id)
        if product is None:
            raise Exception(f"Unknown product: {product_id!r}")
        return PricingService._has_stock(product, quantity)

    @staticmethod
    def _get_product(product_id: str) -> Product | None:
        return PricingService._CATALOG.get(product_id)

    @staticmethod
    def _price_for(
        product: Product, quantity: int, customer_type: str
    ) -> str:
        if quantity <= 0:
            raise Exception(
                f"Quantity must be a positive integer, got {quantity!r}"
            )
        if customer_type not in (PricingService.REGULAR, PricingService.PREMIUM):
            raise Exception(
                f"Unknown customer type: {customer_type!r}"
            )

        discount_percent = 0
        if customer_type == PricingService.PREMIUM:
            discount_percent += PricingService._PREMIUM_DISCOUNT
        if quantity >= PricingService._BULK_THRESHOLD:
            discount_percent += PricingService._BULK_DISCOUNT

        base_cents = product.price_cents * quantity
        total_cents = (base_cents * (100 - discount_percent) + 50) // 100

        # Returned as a JSON string: a primitive that the graft generates AND that
        # the Python consumer can materialize (a dict/custom object cannot).
        return json.dumps(
            {
                "product_id": product.id,
                "unit_price_cents": product.price_cents,
                "quantity": quantity,
                "discount_percent": discount_percent,
                "total_price_cents": total_cents,
            }
        )

    @staticmethod
    def _has_stock(product: Product, quantity: int) -> bool:
        if quantity <= 0:
            raise Exception(
                f"Quantity must be a positive integer, got {quantity!r}"
            )
        return product.stock >= quantity
