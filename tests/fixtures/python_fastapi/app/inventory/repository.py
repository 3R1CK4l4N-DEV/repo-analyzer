from app.inventory.models import Product


class ProductRepository:
    def __init__(self) -> None:
        self._items: dict[str, Product] = {}

    def get(self, sku: str) -> Product | None:
        return self._items.get(sku)

    def save(self, product: Product) -> Product:
        self._items[product.sku] = product
        return product
