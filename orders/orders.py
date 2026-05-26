from pydantic import BaseModel, Field


class OrderItem(BaseModel):
    product: str
    quantity: int
    unit_price: int

    @property
    def subtotal(self) -> int:
        return self.unit_price * self.quantity


class Order(BaseModel):
    items: list[OrderItem] = Field(default_factory=list)

    def add_item(self, product: str, quantity: int, unit_price: int) -> None:
        self.items.append(
            OrderItem(product=product, quantity=quantity, unit_price=unit_price)
        )

    def total(self) -> int:
        return sum(item.subtotal for item in self.items)
