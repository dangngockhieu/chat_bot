import json

from openai import OpenAI

from app.core.config import settings
from app.dto.chat import ExtractedOrderResult
from app.services.menu_service import get_available_items


client = OpenAI(api_key=settings.OPENAI_API_KEY)


def build_menu_context() -> str:
    items = get_available_items()

    lines: list[str] = []
    for item in items:
        lines.append(
            f"{item['item_id']} | {item['name']} | {item['description']} | M={item['price_m']} | L={item['price_l']}"
        )

    return "\n".join(lines)


SYSTEM_PROMPT = """
Bạn là AI hỗ trợ đặt trà sữa qua Telegram.

Nhiệm vụ:
- Đọc tin nhắn khách hàng
- Trích xuất dữ liệu thành JSON
- Chỉ chọn sản phẩm từ MENU được cung cấp
- Nếu map được món, ưu tiên trả về item_id chính xác
- Không tự bịa item_id
- Không tự bịa sản phẩm ngoài menu
- Nếu chưa chắc món nào, để item_id = null và product_name là phần bạn hiểu được
- Nếu khách chỉ đang hỏi menu hoặc giá, intent = "ask_menu"
- Nếu khách đang đặt món, intent = "order"
- Nếu khách đang bổ sung tên/sđt/địa chỉ, intent = "update_info"
- Nếu khách xác nhận đơn, intent = "confirm"
- Nếu khách hủy đơn, intent = "cancel"

Quy tắc:
- size chỉ nhận M hoặc L nếu có
- quantity mặc định là 1 nếu không nói rõ
- Nếu khách nói nhiều món, trả đủ trong items
- Nếu khách có gửi tên, số điện thoại, địa chỉ, note thì trích ra
- confirmation = true nếu khách xác nhận kiểu "ok", "xác nhận", "đồng ý", "thanh toán"
- confirmation = false nếu khách từ chối/chỉnh sửa
- Nếu không liên quan, intent = "unknown"

Chỉ trả về JSON hợp lệ, không thêm markdown, không giải thích.
Schema:
{
  "intent": "order | update_info | confirm | cancel | ask_menu | unknown",
  "items": [
    {
      "item_id": "string | null",
      "product_name": "string | null",
      "size": "M | L | null",
      "quantity": 1
    }
  ],
  "customer_name": "string | null",
  "customer_phone": "string | null",
  "delivery_address": "string | null",
  "note": "string | null",
  "confirmation": true
}
""".strip()


def extract_order_data(user_text: str) -> ExtractedOrderResult:
    menu_context = build_menu_context()

    user_prompt = f"""
MENU:
{menu_context}

TIN NHẮN KHÁCH:
{user_text}
""".strip()

    response = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
    )

    content = (response.choices[0].message.content or "").strip()

    try:
        data = json.loads(content)
        return ExtractedOrderResult(**data)
    except Exception:
        return ExtractedOrderResult(intent="unknown")