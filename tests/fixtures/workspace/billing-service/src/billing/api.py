from fastapi import FastAPI

app = FastAPI()


@app.post("/invoices")
def create_invoice(order: dict) -> dict:
    return {"id": 1}


@app.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: int) -> dict:
    return {"id": invoice_id}
