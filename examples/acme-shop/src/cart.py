# implements: ADR-001


class Cart:
    def __init__(self):
        self.items = {}

    def add(self, sku: str, qty: int = 1) -> None:
        self.items[sku] = self.items.get(sku, 0) + qty

    def count(self) -> int:
        return sum(self.items.values())
