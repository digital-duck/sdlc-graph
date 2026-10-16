# implements: ADR-002
from cart import Cart


def total(cart: Cart, prices: dict) -> float:
    return sum(prices[sku] * qty for sku, qty in cart.items.items())
