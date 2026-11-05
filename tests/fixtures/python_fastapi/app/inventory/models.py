from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel


class StockStatus(str, Enum):
    IN_STOCK = "in_stock"
    OUT_OF_STOCK = "out_of_stock"


class Product(BaseModel):
    """A sellable product tracked in inventory."""

    sku: str
    name: str
    quantity: int = 0
    reorder_level: int = 10


@dataclass
class StockMovement:
    sku: str
    delta: int
