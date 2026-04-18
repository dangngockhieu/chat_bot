from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.services.payos_service import process_payos_return, process_payos_webhook

router = APIRouter(prefix="/payment", tags=["payment"])

@router.post("/payos-webhook")
async def payos_webhook(request: Request, db: Session = Depends(get_db)):
    # Điểm nhận tín hiệu thanh toán từ PayOS
    body = await request.json()

    # Chuyển tiếp dữ liệu cho Service xử lý
    result = await process_payos_webhook(body, db)

    return {"ok": result}


@router.get("/payos-return")
async def payos_return(request: Request, db: Session = Depends(get_db)):
    # Fallback callback khi khách quay lại từ PayOS
    order_code_raw = request.query_params.get("orderCode")
    if not order_code_raw:
        return {"ok": False, "message": "missing orderCode"}

    try:
        order_code = int(order_code_raw)
    except ValueError:
        return {"ok": False, "message": "invalid orderCode"}

    ok = await process_payos_return(order_code=order_code, db=db)
    return {"ok": ok}