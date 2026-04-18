import json
import uuid
from datetime import datetime, timezone

from payos import PayOS
from payos.types import CreatePaymentLinkRequest

from app.core.config import settings
from app.models.order import Order
from app.models.payment import Payment
from app.services.telegram_service import send_message, send_message_to_owner
from sqlalchemy.orm import Session

# Khởi tạo PayOS Client
payos_client = PayOS(
    client_id=settings.PAYOS_CLIENT_ID,
    api_key=settings.PAYOS_API_KEY,
    checksum_key=settings.PAYOS_CHECKSUM_KEY
)


def _generate_payment_ref(order_id: int) -> str:
    # Tạo mã tham chiếu thanh toán nội bộ dạng UUID
    return f"ord{order_id}-{uuid.uuid4().hex}"


def _generate_payos_order_code_from_ref(payment_ref: str, salt: int = 0) -> int:
    """
    PayOS dùng order_code kiểu số. Hàm này chuyển payment_ref (UUID) sang mã số duy nhất.
    Giới hạn ở 15 chữ số để an toàn khi serialize qua nhiều hệ thống.
    """
    hex_part = payment_ref.split("-")[-1][:12]
    base = int(hex_part, 16)
    numeric_code = (base * 10 + salt) % 900_000_000_000_000
    if numeric_code < 100_000_000_000_000:
        numeric_code += 100_000_000_000_000
    return numeric_code


def _save_payment_record(
    db: Session,
    order: Order,
    payment_ref: str,
    payos_order_code: int,
    payos_res,
) -> None:
    raw_response = payos_res.model_dump() if hasattr(payos_res, "model_dump") else {}
    raw_response["payment_ref"] = payment_ref
    raw_response["payos_order_code"] = payos_order_code

    payment = Payment(
        order_id=order.id,
        provider="payos",
        amount=order.total_amount,
        status="pending",
        payment_link_id=getattr(payos_res, "id", None) or getattr(payos_res, "paymentLinkId", None),
        checkout_url=getattr(payos_res, "checkout_url", None) or getattr(payos_res, "checkoutUrl", None),
        qr_code=getattr(payos_res, "qr_code", None) or getattr(payos_res, "qrCode", None),
        # Dùng order_code số của PayOS để map webhook nhanh và chính xác.
        provider_transaction_id=str(payos_order_code),
        raw_response_json=json.dumps(
            raw_response,
            ensure_ascii=False,
        ),
    )
    db.add(payment)
    db.commit()

# Xử lý webhook từ PayOS khi có sự kiện thanh toán
def create_checkout_link(order: Order, db: Session | None = None) -> str:
    bot_url = f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}" if settings.TELEGRAM_BOT_USERNAME else "https://t.me/"
    callback_url = f"{settings.APP_BASE_URL.rstrip('/')}/payment/payos-return" if settings.APP_BASE_URL else bot_url

    last_error: Exception | None = None
    payment_ref = _generate_payment_ref(order.id)
    for attempt in range(3):
        payos_order_code = _generate_payos_order_code_from_ref(payment_ref, salt=attempt)
        payment_request = CreatePaymentLinkRequest(
            order_code=payos_order_code,
            amount=order.total_amount,
            description=f"TT {order.order_code[-6:]} {payment_ref[-6:]}",
            cancel_url=callback_url,
            return_url=callback_url,
        )

        try:
            payos_res = payos_client.payment_requests.create(payment_request)

            if db:
                _save_payment_record(
                    db=db,
                    order=order,
                    payment_ref=payment_ref,
                    payos_order_code=payos_order_code,
                    payos_res=payos_res,
                )

            checkout_url = getattr(payos_res, "checkout_url", None) or getattr(payos_res, "checkoutUrl", None)
            if not checkout_url:
                raise RuntimeError("PayOS không trả về checkout URL")
            return checkout_url
        except Exception as exc:
            last_error = exc
            error_text = str(exc).lower()
            if "đơn thanh toán đã tồn tại" in error_text or "already exists" in error_text:
                continue
            raise

    if last_error:
        raise last_error
    raise RuntimeError("Không thể tạo link thanh toán")

# XỬ LÝ WEBHOOK VÀ CALLBACK TỪ PAYOS
async def process_payos_webhook(payload: dict, db: Session):
    # Trích xuất dữ liệu từ PayOS
    success = payload.get("success")
    data = payload.get("data", {})
    payos_order_code = data.get("orderCode")

    if not success or not payos_order_code:
        return False

    payment = (
        db.query(Payment)
        .filter(
            Payment.provider == "payos",
            Payment.provider_transaction_id == str(payos_order_code),
        )
        .order_by(Payment.id.desc())
        .first()
    )

    # Backward compatibility cho các đơn cũ dùng order.id làm orderCode.
    order = payment.order if payment else db.query(Order).filter(Order.id == payos_order_code).first()

    payment_just_paid = False
    if payment and payment.status != "paid":
        payment.status = "paid"
        payment.paid_at = datetime.now(timezone.utc)
        payment.raw_response_json = json.dumps(payload, ensure_ascii=False)
        db.commit()
        payment_just_paid = True

    # Tìm và cập nhật đơn hàng
    order_just_paid = False
    if order and order.status == "draft":
        order.status = "paid"
        db.commit()
        order_just_paid = True

    # Chỉ gửi thông báo khi có sự kiện thanh toán mới để tránh spam do webhook lặp.
    should_notify = bool(order and (order_just_paid or payment_just_paid))
    if should_notify:

        # Soạn nội dung chi tiết đơn hàng
        items_text = "\n".join([f"- {i.product_name} ({i.size}): x{i.quantity}" for i in order.items])

        report_text = (
            "🔔 **ĐƠN HÀNG MỚI ĐÃ THANH TOÁN**\n\n"
            f"👤 Khách: {order.customer_name}\n"
            f"📞 SĐT: {order.customer_phone}\n"
            f"📍 Địa chỉ: {order.delivery_address}\n"
            "------------------\n"
            f"🛒 Chi tiết:\n{items_text}\n"
            "------------------\n"
            f"💰 **Tổng: {order.total_amount:,}đ**"
        )

        # Gửi thông báo
        owner_chat_id = str(settings.TELEGRAM_OWNER_CHAT_ID)
        customer_chat_id = str(order.platform_user_id)
        owner_report_text = report_text

        # Nếu chính chủ quán đang đặt hàng, gắn nhãn để dễ phân biệt với tin xác nhận khách.
        if owner_chat_id == customer_chat_id:
            owner_report_text = f"🧾 CÓ ĐƠN HÀNG MỚI\n\n{report_text}"

        try:
            await send_message_to_owner(owner_report_text)
        except Exception as exc:
            # Không làm hỏng webhook nếu tin nhắn quản trị thất bại.
            print(f"Lỗi gửi thông báo cho chủ quán: {exc}")

        try:
            await send_message(customer_chat_id, "Thanh toán thành công! Đơn của bạn đang được xử lý.")
        except Exception as exc:
            print(f"Lỗi gửi xác nhận cho khách: {exc}")

        return True
    return False

# Fallback khi PayOS redirect về return_url: xác minh trạng thái thật rồi tái dùng luồng webhook.
async def process_payos_return(order_code: int, db: Session) -> bool:
    try:
        info = payos_client.getPaymentLinkInformation(order_code)
        status = (getattr(info, "status", "") or "").strip().lower()
        is_paid = status in {"paid", "completed", "success", "succeeded"}

        if not is_paid:
            return False

        payload = {
            "success": True,
            "data": {
                "orderCode": order_code,
            },
        }
        return await process_payos_webhook(payload, db)
    except Exception as exc:
        print(f"Lỗi process_payos_return: {exc}")
        return False