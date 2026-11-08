"""A tiny shopping cart. It has one bug for the Contract demo."""


def apply_discount(total: float, percent: int) -> float:
    """Return the total after a percent discount, rounded to cents."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    # Bug: the discount is applied twice.
    discounted = total * (1 - percent / 100)
    discounted = discounted * (1 - percent / 100)
    return round(discounted, 2)


def cart_total(prices: list, percent: int = 0) -> float:
    return apply_discount(sum(prices), percent)
