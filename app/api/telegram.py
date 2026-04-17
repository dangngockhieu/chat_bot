from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.dto.order import AddOrderItemRequest, UpdateCustomerInfoRequest
from app.services.ai_service import extract_order_data
from app.services.menu_service import (
    find_item_by_id,
    find_item_by_name,
    get_all_items,
    get_price_by_size,
    is_item_available,
)
from app.services.order_service import (
    add_item_to_order,
    get_or_create_draft_order,
    is_order_ready_for_payment,
    update_order_customer_info,
)
from app.services.telegram_service import extract_message_data, send_message

router = APIRouter(prefix="/telegram", tags=["telegram"])


def build_menu_by_category() -> list[str]:
    items = get_all_items()

    if not items:
        return ["Hiện tại menu đang trống."]

    grouped: dict[str, list[dict]] = {}

    for item in items:
        if not item["available"]:
            continue
        category = item["category"] or "Khác"
        grouped.setdefault(category, []).append(item)

    messages: list[str] = []

    for category, category_items in grouped.items():
        lines = [f"{category}:"]
        for item in category_items:
            lines.append(
                f"- {item['item_id']} | {item['name']} | M: {item['price_m']}đ | L: {item['price_l']}đ"
            )
        messages.append("\n".join(lines))

    messages.append("Bạn có thể nhắn tự nhiên để đặt món, ví dụ: 2 trà sữa trân châu đen size L")
    return messages


def format_order_summary(order) -> str:
    lines = [f"Đơn hàng {order.order_code}:"]
    for item in order.items:
        lines.append(
            f"- {item.product_name} | size {item.size or '-'} | SL: {item.quantity} | {item.line_total}đ"
        )

    lines.append(f"Tổng tiền: {order.total_amount}đ")
    lines.append(f"Tên: {order.customer_name or 'Chưa có'}")
    lines.append(f"SĐT: {order.customer_phone or 'Chưa có'}")
    lines.append(f"Địa chỉ: {order.delivery_address or 'Chưa có'}")

    if order.note:
        lines.append(f"Ghi chú: {order.note}")

    return "\n".join(lines)


def get_missing_fields_text(order) -> str | None:
    missing = []

    if not order.customer_name:
        missing.append("tên người nhận")
    if not order.customer_phone:
        missing.append("số điện thoại")
    if not order.delivery_address:
        missing.append("địa chỉ giao hàng")

    if not missing:
        return None

    return "Mình cần thêm: " + ", ".join(missing) + "."


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    update = await request.json()
    data = extract_message_data(update)

    if not data:
        return {"ok": True, "message": "No message found"}

    if not data.text:
        await send_message(data.chat_id, "Hiện tại mình chỉ hỗ trợ tin nhắn văn bản.")
        return {"ok": True}

    text = data.text.strip().lower()

    if text == "/start":
        welcome_text = (
            "Xin chào, mình là bot đặt trà sữa.\n"
            "Bạn có thể nhắn món muốn đặt, ví dụ:\n"
            "- 2 trà sữa trân châu đen size L\n"
            "- mình tên Khiếu, số điện thoại 09..., địa chỉ ...\n"
            "- menu"
        )
        await send_message(data.chat_id, welcome_text)
        return {"ok": True}

    if text in {"menu", "/menu"}:
        messages = build_menu_by_category()
        for message_text in messages:
            await send_message(data.chat_id, message_text)
        return {"ok": True}

    order = get_or_create_draft_order(
        db=db,
        platform_user_id=data.user_id,
        platform="telegram",
    )

    try:
        extracted = extract_order_data(data.text)
    except Exception:
        await send_message(
            data.chat_id,
            "Hệ thống AI đang tạm bận hoặc hết quota. Bạn thử lại sau nhé."
        )
        return {"ok": True}

    if extracted.intent == "ask_menu":
        messages = build_menu_by_category()
        for message_text in messages:
            await send_message(data.chat_id, message_text)
        return {"ok": True}

    if extracted.intent == "cancel":
        await send_message(data.chat_id, "Mình đã ghi nhận yêu cầu hủy/chỉnh đơn. Bạn nhắn lại món muốn đặt nhé.")
        return {"ok": True}

    if extracted.customer_name or extracted.customer_phone or extracted.delivery_address or extracted.note:
        info_payload = UpdateCustomerInfoRequest(
            customer_name=extracted.customer_name,
            customer_phone=extracted.customer_phone,
            delivery_address=extracted.delivery_address,
            note=extracted.note,
        )
        order = update_order_customer_info(db=db, order=order, payload=info_payload)

    added_count = 0
    invalid_items: list[str] = []

    for extracted_item in extracted.items:
        item = None

        if extracted_item.item_id:
            item = find_item_by_id(extracted_item.item_id)

        if not item and extracted_item.product_name:
            item = find_item_by_name(extracted_item.product_name)

        if not item:
            invalid_items.append(extracted_item.product_name or extracted_item.item_id or "món không xác định")
            continue

        if not is_item_available(item):
            invalid_items.append(item["name"])
            continue

        size = (extracted_item.size or "M").upper()
        if size not in {"M", "L"}:
            size = "M"

        unit_price = get_price_by_size(item, size)

        item_payload = AddOrderItemRequest(
            product_name=item["name"],
            size=size,
            unit_price=unit_price,
            quantity=extracted_item.quantity,
            toppings=[],
        )

        add_item_to_order(db=db, order=order, payload=item_payload)
        db.refresh(order)
        added_count += 1

    if invalid_items:
        await send_message(
            data.chat_id,
            "Mình chưa xác định được các món này: " + ", ".join(invalid_items),
        )

    if extracted.intent == "confirm" or extracted.confirmation:
        if is_order_ready_for_payment(order):
            summary = format_order_summary(order)
            await send_message(
                data.chat_id,
                summary + "\n\nĐơn đã đủ thông tin. Bước tiếp theo mình sẽ tạo link thanh toán.",
            )
            return {"ok": True}

        missing_text = get_missing_fields_text(order)
        await send_message(
            data.chat_id,
            f"Đơn hiện chưa đủ thông tin.\n{missing_text or ''}\n\n{format_order_summary(order)}",
        )
        return {"ok": True}

    if added_count > 0:
        summary = format_order_summary(order)
        missing_text = get_missing_fields_text(order)

        if missing_text:
            await send_message(
                data.chat_id,
                f"Mình đã thêm món vào đơn.\n\n{summary}\n\n{missing_text}",
            )
        else:
            await send_message(
                data.chat_id,
                f"Mình đã thêm món vào đơn.\n\n{summary}\n\nNếu đúng rồi bạn nhắn 'xác nhận' để mình chuẩn bị bước thanh toán.",
            )
        return {"ok": True}

    if extracted.intent == "update_info":
        summary = format_order_summary(order)
        missing_text = get_missing_fields_text(order)

        if missing_text:
            await send_message(
                data.chat_id,
                f"Mình đã cập nhật thông tin.\n\n{summary}\n\n{missing_text}",
            )
        else:
            await send_message(
                data.chat_id,
                f"Mình đã cập nhật thông tin.\n\n{summary}\n\nNếu đúng rồi bạn nhắn 'xác nhận'.",
            )
        return {"ok": True}

    await send_message(
        data.chat_id,
        "Mình chưa hiểu rõ yêu cầu. Bạn có thể nhắn ví dụ: '2 trà sữa trân châu đen size L' hoặc 'mình tên ..., số điện thoại ..., địa chỉ ...'",
    )
    return {"ok": True}