import json

from fastapi import APIRouter, Depends, Request, BackgroundTasks
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.database import get_db, SessionLocal
from app.dto.order import AddOrderItemRequest, UpdateCustomerInfoRequest
from app.services.ai_service import AIQuotaExceededError, extract_order_data
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
    clear_order_items,
    recalculate_order_total
)

from app.models.order_item import OrderItem
from app.services.telegram_service import extract_message_data, send_message

from app.services.payos_service import create_checkout_link

router = APIRouter(prefix="/telegram", tags=["telegram"])

ADD_KEYWORDS = [
    "thêm",
    "them",
    "lấy thêm",
    "lay them",
    "add",
    "nữa",
    "nua",
    "cộng",
    "cong",
    "kèm",
    "kem",
]

MODIFY_KEYWORDS = [
    "xóa",
    "xoa",
    "bỏ",
    "bo",
    "bớt",
    "bot",
    "không lấy",
    "khong lay",
    "đổi",
    "doi",
    "thay",
    "remove",
    "delete",
]


def _normalized_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _contains_any_keyword(text: str, keywords: list[str]) -> bool:
    normalized = _normalized_text(text)
    return any(keyword in normalized for keyword in keywords)


def _is_replace_all_new_list_case(order, extracted, user_text: str) -> bool:
    if not order.items:
        return False
    if extracted.intent != "order" or not extracted.items:
        return False
    if getattr(extracted, "replace_all", False):
        return False

    actions = [getattr(i, "action", "add") for i in extracted.items]
    if any(action in ["remove", "substitute", "removeall"] for action in actions):
        return False

    if _contains_any_keyword(user_text, ADD_KEYWORDS):
        return False
    if _contains_any_keyword(user_text, MODIFY_KEYWORDS):
        return False

    return True


def _remove_toppings_from_item(existing_item: OrderItem, toppings_to_remove: list[dict]) -> bool:
    """Xóa topping khỏi một dòng món và cập nhật lại giá tiền dòng đó."""
    if not existing_item.toppings_json:
        return False

    try:
        current_toppings = json.loads(existing_item.toppings_json)
    except json.JSONDecodeError:
        return False

    if not isinstance(current_toppings, list) or not current_toppings:
        return False

    remove_names = {
        (t.get("name") or "").strip().lower()
        for t in toppings_to_remove
        if t.get("name")
    }
    remove_item_ids = {
        str(t.get("item_id")).strip().upper()
        for t in toppings_to_remove
        if t.get("item_id")
    }

    if not remove_names and not remove_item_ids:
        return False

    kept_toppings = []
    removed_total = 0

    for topping in current_toppings:
        top_name = (topping.get("name") or "").strip().lower()
        top_item_id = str(topping.get("item_id") or "").strip().upper()
        should_remove = (top_name and top_name in remove_names) or (top_item_id and top_item_id in remove_item_ids)

        if should_remove:
            removed_total += int(topping.get("price", 0) or 0)
            continue
        kept_toppings.append(topping)

    if len(kept_toppings) == len(current_toppings):
        return False

    existing_item.toppings_json = json.dumps(kept_toppings, ensure_ascii=False)
    existing_item.unit_price = max(existing_item.unit_price - removed_total, 0)
    existing_item.line_total = existing_item.unit_price * existing_item.quantity
    return True


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
    if not order.customer_name: missing.append("tên người nhận")
    if not order.customer_phone: missing.append("số điện thoại")
    if not order.delivery_address: missing.append("địa chỉ giao hàng")
    if not missing: return None
    return "Mình cần thêm: " + ", ".join(missing) + "."

# HÀM CHẠY NGẦM
async def process_order_logic(data):
    """Hàm này chạy ngầm dưới background, không làm kẹt Webhook của Telegram"""

    # TẠO SESSION DATABASE MỚI CHO BACKGROUND TASK
    db = SessionLocal()

    try:
        order = get_or_create_draft_order(db=db, platform_user_id=data.user_id)

        try:
            extracted = extract_order_data(data.text)
        except AIQuotaExceededError:
            await send_message(
                data.chat_id,
                "Hệ thống đang quá tải lượt xử lý AI. Bạn vui lòng thử lại sau khoảng 1 phút nhé.",
            )
            return
        except Exception:
            await send_message(data.chat_id, "Hệ thống AI đang tạm bận. Bạn thử lại sau nhé.")
            return

        # XỬ LÝ LÀM LẠI TỪ ĐẦU (REPLACE ALL)
        should_replace_all = (
            getattr(extracted, "replace_all", False)
            or _is_replace_all_new_list_case(order=order, extracted=extracted, user_text=data.text)
        )
        if extracted.intent == "cancel" or should_replace_all:
            clear_order_items(db, order)
            db.refresh(order)
            if extracted.intent == "cancel":
                await send_message(data.chat_id, "Mình đã hủy đơn cũ. Bạn nhắn lại món mới nhé.")
                return

        if extracted.intent == "ask_menu":
            for msg in build_menu_by_category(): await send_message(data.chat_id, msg)
            return

        if extracted.customer_name or extracted.customer_phone or extracted.delivery_address or extracted.note:
            info_payload = UpdateCustomerInfoRequest(
                customer_name=extracted.customer_name, customer_phone=extracted.customer_phone,
                delivery_address=extracted.delivery_address, note=extracted.note,
            )
            order = update_order_customer_info(db=db, order=order, payload=info_payload)

        # VÒNG LẶP XỬ LÝ TỪNG ACTION CỤ THỂ
        added_count = 0
        removed_or_sub_count = 0 # Biến cờ theo dõi việc xóa/sửa
        invalid_items: list[str] = []

        for extracted_item in extracted.items:
            action = getattr(extracted_item, 'action', 'add')

            # NHÁNH XÓA/GIẢM/THAY THẾ (REMOVE / SUBSTITUTE)
            if action in ["remove", "substitute", "removeall"]:
                removed_or_sub_count += 1

                if action == "removeall":
                    clear_order_items(db, order)
                    db.refresh(order)
                    continue

                # Xóa topping (khách có thể nói bỏ topping theo món hoặc chỉ nêu topping)
                topping_remove_candidates: list[dict] = []
                if getattr(extracted_item, "toppings", None):
                    for t in extracted_item.toppings:
                        t_item = None
                        if getattr(t, "item_id", None):
                            t_item = find_item_by_id(t.item_id)
                        elif getattr(t, "topping_name", None):
                            t_item = find_item_by_name(t.topping_name)
                        if t_item:
                            topping_remove_candidates.append(
                                {"item_id": t_item["item_id"], "name": t_item["name"]}
                            )

                # Trường hợp khách chỉ yêu cầu bỏ topping, không nêu món chính
                if (
                    action == "remove"
                    and topping_remove_candidates
                    and not extracted_item.item_id
                    and not extracted_item.product_name
                ):
                    changed = False
                    for existing in db.query(OrderItem).filter(OrderItem.order_id == order.id).all():
                        if _remove_toppings_from_item(existing, topping_remove_candidates):
                            db.add(existing)
                            changed = True
                    if changed:
                        db.commit()
                    continue

                query = db.query(OrderItem).filter(OrderItem.order_id == order.id)

                existing_item = None
                if extracted_item.item_id:
                    menu_item = find_item_by_id(extracted_item.item_id)
                    if menu_item:
                        existing_item = query.filter(
                            OrderItem.product_name.ilike(f"%{menu_item['name']}%")
                        ).first()

                if not existing_item and extracted_item.product_name:
                    existing_item = query.filter(
                        OrderItem.product_name.ilike(f"%{extracted_item.product_name}%")
                    ).first()

                if existing_item:
                    if action == "remove" and topping_remove_candidates:
                        if _remove_toppings_from_item(existing_item, topping_remove_candidates):
                            db.add(existing_item)
                            db.commit()
                        continue

                    if action == "remove":
                        if existing_item.quantity > extracted_item.quantity:
                            existing_item.quantity -= extracted_item.quantity
                            existing_item.line_total = existing_item.unit_price * existing_item.quantity
                            db.commit()
                        else:
                            db.delete(existing_item)
                            db.commit()
                        continue

                    elif action == "substitute":
                        db.delete(existing_item)
                        db.commit()
                        continue

            # --- NHÁNH THÊM MÓN ---
            item = None
            if extracted_item.item_id:
                item = find_item_by_id(extracted_item.item_id)
            elif extracted_item.product_name:
                item = find_item_by_name(extracted_item.product_name)

            # Hỗ trợ đặt topping riêng (không có món chính), ví dụ: "cho 2 trân châu"
            if not item and not extracted_item.product_name and getattr(extracted_item, "toppings", None):
                for t in extracted_item.toppings:
                    t_item = None
                    if getattr(t, "item_id", None):
                        t_item = find_item_by_id(t.item_id)
                    elif getattr(t, "topping_name", None):
                        t_item = find_item_by_name(t.topping_name)

                    if not t_item or not is_item_available(t_item):
                        invalid_items.append(getattr(t, "topping_name", None) or "topping không xác định")
                        continue

                    topping_payload = AddOrderItemRequest(
                        product_name=t_item["name"],
                        size="M",
                        unit_price=t_item["price_m"],
                        quantity=max(getattr(t, "quantity", 1), 1),
                        toppings=[],
                    )
                    add_item_to_order(db=db, order=order, payload=topping_payload)
                    added_count += 1
                continue

            if not item or not is_item_available(item):
                if action != "remove":
                    invalid_items.append(extracted_item.product_name or "món không xác định")
                continue

            size = (extracted_item.size or "M").upper()
            unit_price = get_price_by_size(item, size)

            item_toppings = []
            if getattr(extracted_item, 'toppings', None):
                for t in extracted_item.toppings:
                    t_item = None
                    if getattr(t, 'item_id', None):
                        t_item = find_item_by_id(t.item_id)
                    elif getattr(t, 'topping_name', None):
                        t_item = find_item_by_name(t.topping_name)

                    if t_item:
                        item_toppings.append({"item_id": t_item["item_id"], "name": t_item["name"], "price": t_item["price_m"]})

            item_payload = AddOrderItemRequest(
                product_name=item["name"], size=size, unit_price=unit_price,
                quantity=extracted_item.quantity, toppings=item_toppings,
            )
            add_item_to_order(db=db, order=order, payload=item_payload)
            added_count += 1

        db.refresh(order)
        recalculate_order_total(db, order)

        if invalid_items:
            await send_message(data.chat_id, f"Mình chưa rõ món: {', '.join(invalid_items)}")

        # --- XỬ LÝ XÁC NHẬN ---
        if extracted.intent == "confirm" or extracted.confirmation:
            if is_order_ready_for_payment(order):
                summary = format_order_summary(order)
                await send_message(data.chat_id, "⏳ Đang tạo mã thanh toán...")
                try:
                    checkout_url = create_checkout_link(order, db=db)
                    await send_message(data.chat_id, f"{summary}\n\n💳 Link thanh toán:\n{checkout_url}")
                except Exception:
                    await send_message(data.chat_id, "Lỗi tạo thanh toán, thử lại sau nhé.")
                return

            missing_text = get_missing_fields_text(order)
            await send_message(data.chat_id, f"Chưa đủ thông tin!\n{missing_text or ''}\n\n{format_order_summary(order)}")
            return

        # --- PHẢN HỒI NẾU CÓ THAY ĐỔI ---
        if added_count > 0 or extracted.intent == "update_info" or removed_or_sub_count > 0:
            summary = format_order_summary(order)
            missing_text = get_missing_fields_text(order)
            suffix = missing_text if missing_text else "Nếu đúng rồi bạn nhắn 'xác nhận'."
            await send_message(data.chat_id, f"Mình đã cập nhật đơn hàng:\n\n{summary}\n\n{suffix}")
            return

        await send_message(data.chat_id, "Bạn muốn đặt thêm gì không?")

    except OperationalError as exc:
        # Kết nối DB cloud có thể rớt SSL tạm thời. Rollback để clear transaction lỗi.
        db.rollback()
        print(f"DB OperationalError in process_order_logic: {exc}")
        await send_message(
            data.chat_id,
            "Kết nối dữ liệu đang chập chờn, bạn vui lòng nhắn lại trong ít giây nhé.",
        )

    except Exception as exc:
        db.rollback()
        print(f"Unhandled error in process_order_logic: {exc}")
        await send_message(
            data.chat_id,
            "Hệ thống đang bận xử lý, bạn thử lại giúp mình nhé.",
        )

    finally:
        # BẮT BUỘC ĐÓNG KẾT NỐI DB KHI CHẠY XONG
        db.close()


# API WEBHOOK
@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
):
    update = await request.json()
    data = extract_message_data(update)

    if not data or not data.text:
        if data: await send_message(data.chat_id, "Hiện tại mình chỉ hỗ trợ tin nhắn văn bản.")
        return {"ok": True}

    text = data.text.strip().lower()

    # Xử lý các lệnh đơn giản ngay lập tức
    if text == "/start":
        await send_message(data.chat_id, "Xin chào, mình là bot đặt trà sữa...\n")
        return {"ok": True}

    if text in {"menu", "/menu"}:
        messages = build_menu_by_category()
        for msg in messages: await send_message(data.chat_id, msg)
        return {"ok": True}

    # THÊM LOGIC GỌI AI VÀO BACKGROUND TASK CHẠY NGẦM
    background_tasks.add_task(process_order_logic, data)

    # TRẢ VỀ OK CHO TELEGRAM NGAY LẬP TỨC ĐỂ CHẶN SPAM
    return {"ok": True}