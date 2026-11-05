from fastapi import APIRouter, Depends, FastAPI

from app.inventory.models import Product, StockMovement
from app.inventory.service import InventoryService

app = FastAPI(title="Inventory")
router = APIRouter(prefix="/products")


def get_service() -> InventoryService:
    raise NotImplementedError


@router.get("/{sku}", response_model=Product)
def read_product(sku: str, service: InventoryService = Depends(get_service)) -> Product:
    return service.repository.get(sku)


@router.post("/{sku}/adjust")
async def adjust(sku: str, movement: StockMovement, service: InventoryService = Depends(get_service)) -> Product:
    return service.adjust_stock(movement)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


app.include_router(router)
