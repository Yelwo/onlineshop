import factory

from pricing import Product


class ProductFactory(factory.Factory):
    """Builds Product objects for seeding the in-memory catalog in tests."""

    class Meta:
        model = Product

    id = factory.Sequence(lambda n: f"prod-{n}")
    name = factory.Sequence(lambda n: f"Product {n}")
    price_cents = 10000  # 100.00
    stock = 10
