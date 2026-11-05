from .models import Product, StockMovement
from .repository import ProductRepository

MAX_SINGLE_ADJUSTMENT = 1000


class InventoryService:
    """Business operations on stock levels."""

    def __init__(self, repository: ProductRepository):
        self.repository = repository

    def adjust_stock(self, movement: StockMovement) -> Product:
        """Business rule: stock can never go negative and a single adjustment is capped."""
        if abs(movement.delta) > MAX_SINGLE_ADJUSTMENT:
            raise ValueError("adjustment too large")
        product = self.repository.get(movement.sku)
        if product is None:
            raise LookupError(movement.sku)
        if product.quantity + movement.delta < 0:
            raise ValueError("insufficient stock")
        product.quantity += movement.delta
        return self.repository.save(product)

    def needs_reorder(self, sku: str) -> bool:
        product = self.repository.get(sku)
        return product is not None and product.quantity <= product.reorder_level
