"""The customer's cart. It holds dishes from ONE restaurant on ONE platform, like the real apps."""


class Cart:
    def __init__(self) -> None:
        self.platform_id: str | None = None
        self.restaurant_id: str | None = None
        self.restaurant_name: str = ""
        self.items: dict[str, int] = {}  # exact dish name -> quantity, in the order they were added

    def is_empty(self) -> bool:
        return not self.items

    def count(self) -> int:
        return sum(self.items.values())

    def same_place(self, platform_id: str, restaurant_id: str) -> bool:
        return self.is_empty() or (self.platform_id, self.restaurant_id) == (platform_id, restaurant_id)

    def add(self, platform_id: str, restaurant_id: str, restaurant_name: str, item: str, qty: int = 1) -> None:
        if self.is_empty():
            self.platform_id, self.restaurant_id, self.restaurant_name = platform_id, restaurant_id, restaurant_name
        self.items[item] = self.items.get(item, 0) + max(1, int(qty))

    def remove(self, item: str, qty: int = 0) -> None:
        """qty 0 removes the dish completely; otherwise that many are taken out."""
        if item not in self.items:
            return
        left = 0 if qty <= 0 else self.items[item] - qty
        if left > 0:
            self.items[item] = left
        else:
            del self.items[item]
        if self.is_empty():
            self.clear()

    def clear(self) -> None:
        self.platform_id = self.restaurant_id = None
        self.restaurant_name = ""
        self.items = {}

    def qty(self, platform_id: str, restaurant_id: str, item: str) -> int:
        if (self.platform_id, self.restaurant_id) != (platform_id, restaurant_id):
            return 0
        return self.items.get(item, 0)

    def describe(self) -> str:
        if self.is_empty():
            return "empty"
        dishes = ", ".join(f"{q} × {n}" for n, q in self.items.items())
        return f"{dishes} from {self.restaurant_name} on {self.platform_id.title()}"