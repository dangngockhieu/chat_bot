import json
import logging
from typing import Optional

from langchain_community.document_loaders.csv_loader import CSVLoader
from langchain_qdrant import QdrantVectorStore
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate

from app.core.config import settings
from app.dto.chat import ExtractedOrderResult


class AIQuotaExceededError(Exception):
  """Raised when Gemini API returns RESOURCE_EXHAUSTED / quota errors."""


logger = logging.getLogger(__name__)

# --- CẤU HÌNH RAG VỚI QDRANT ---
embeddings = GoogleGenerativeAIEmbeddings(
    model="gemini-embedding-001",
    google_api_key=settings.GEMINI_API_KEY
)

def initialize_vector_db():
    loader = CSVLoader(file_path="data/menu.csv", encoding="utf-8-sig")
    documents = loader.load()

    qdrant = QdrantVectorStore.from_documents(
        documents,
        embeddings,
        path="./qdrant_data",
        collection_name="milktea_menu",
        force_recreate=True
    )
    return qdrant


def initialize_retriever_safely():
    try:
        db = initialize_vector_db()
        return db, db.as_retriever(search_kwargs={"k": 5})
    except Exception as e:
        # Do not crash app startup if Gemini embedding quota is exhausted.
        logger.exception("Cannot initialize vector DB at startup: %s", e)
        return None, None


vector_db, retriever = initialize_retriever_safely()

llm = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0,
    google_api_key=settings.GEMINI_API_KEY
)

# --- PROMPT CHO GEMINI ---
SYSTEM_PROMPT = """
Bạn là AI hỗ trợ đặt trà sữa qua Telegram.

Nhiệm vụ:
- Đọc tin nhắn khách hàng
- Trích xuất dữ liệu thành JSON
- Chỉ chọn sản phẩm từ MENU được cung cấp
- Nếu map được món, ưu tiên trả về item_id chính xác
- Không tự bịa item_id
- Không tự bịa sản phẩm ngoài menu
- Nếu chưa chắc món nào, để item_id = null và product_name là phần bạn hiểu được từ tin nhắn để AI có thể hỏi lại khách sau.

[CẤP ĐỘ ĐƠN HÀNG - REPLACE ALL] (Làm lại toàn bộ đơn):
Gán `intent`: "order" và `replace_all`: true khi khách muốn xóa sạch đơn cũ để đặt lại từ đầu. Xảy ra trong 2 trường hợp:
1. Có từ ngữ phủ định toàn bộ đơn trước đó (Ví dụ: "Thôi bỏ hết đi lấy 1 trà đào", "Đổi ý rồi, cho 2 cà phê", "Làm lại đơn mới nhé").
2. Khách nhắn một danh sách món hoàn toàn mới, độc lập, không liên quan đến ngữ cảnh trước đó và TUYỆT ĐỐI KHÔNG dùng các từ nối chỉ sự thêm vào (như "thêm", "và", "lấy thêm", "cùng với"). Bạn phải tự phân tích ngữ cảnh: Nếu có dấu hiệu khách đang muốn "đập đi xây lại" thay vì mua thêm, hãy bật `replace_all`: true.

[CẤP ĐỘ MÓN - ACTION] (Xử lý từng món cụ thể):
Mỗi sản phẩm (bao gồm cả nước và topping) trong mảng `items` BẮT BUỘC phải gán một trường `action`:

- "add" (Mặc định): Đặt món mới, thêm topping, hoặc tăng số lượng.
  + Ví dụ 1: "Cho 1 trà sữa" -> action="add", quantity=1.
  + Ví dụ 2: "Thêm trân châu đen" -> action="add" (nằm trong mảng toppings của món chính).
  + Ví dụ 3: "Lấy thêm 1 ly trà dâu nữa" -> action="add".

- "remove": Khách muốn xóa hẳn một món, bỏ topping, hoặc giảm số lượng.
  + Ví dụ 1 (Giảm SL): "Bớt 1 ly trà sữa" -> action="remove", quantity=1.
  + Ví dụ 2 (Xóa món): "Không lấy trà sữa nữa" -> action="remove".
  + Ví dụ 3 (Bỏ topping): "Bỏ trân châu đi" -> action="remove" (nằm trong mảng toppings).

- "substitute": Khách yêu cầu đổi món này lấy món khác, hoặc đổi size món đang có.
  + Ví dụ 1: "Thay trà sữa bằng trà đào"
  + Ví dụ 2: "Đổi ly size M nãy thành size L nhé"
  + QUY TẮC BẮT BUỘC: Khi gặp "substitute", bạn PHẢI trả về 2 object riêng biệt cho hành động này:
      Object 1: Món cũ bị đổi đi -> gán `action`="remove"
      Object 2: Món mới/size mới được thêm vào -> gán `action`="add"

Quy tắc bổ sung về Topping (QUAN TRỌNG):
- Phân loại dựa trên Category trong Menu: Món có category "Topping" là đồ thêm.
- Topping PHẢI được đưa vào mảng `toppings` của món chính đi kèm (ví dụ: "Trà sữa thêm trân châu").
- Không tách Topping thành item riêng lẻ trong mảng `items` trừ khi khách mua mỗi topping.

Quy tắc chung:
- intent: order | update_info | confirm | cancel | ask_menu | unknown
- Nếu khách chỉ đang hỏi menu hoặc giá, intent = "ask_menu"
- Nếu khách đang đặt món, intent = "order"
- Nếu khách đang bổ sung tên/sđt/địa chỉ, intent = "update_info"
- Nếu khách xác nhận đơn, intent = "confirm"
- Nếu khách hủy đơn, intent = "cancel"
- size chỉ nhận M hoặc L nếu có
- quantity mặc định là 1 nếu không nói rõ
- Nếu khách nói nhiều món, trả đủ trong items
- Nếu khách có gửi tên, số điện thoại, địa chỉ, note thì trích ra
- confirmation = true nếu khách xác nhận kiểu "ok", "xác nhận", "đồng ý", "thanh toán"
- confirmation = false nếu khách từ chối/chỉnh sửa
- Nếu không liên quan, intent = "unknown"

Chỉ trả về JSON hợp lệ, không thêm markdown, không giải thích.
Schema:
{{
  "intent": "order | update_info | confirm | cancel | ask_menu | unknown",
  "replace_all": bool,
  "items": [
    {{
      "item_id": "string | null",
      "action": "add | remove | removeall | substitute",
      "product_name": "string | null",
      "size": "M | L | null",
      "quantity": 1,
      "toppings": [
        {{
          "item_id": "string | null",
          "topping_name": "string | null",
          "quantity": 1
        }}
      ]
    }}
  ],
  "customer_name": "string | null",
  "customer_phone": "string | null",
  "delivery_address": "string | null",
  "note": "string | null",
  "confirmation": true
}}
""".strip()

# Đẩy System Prompt vào, và kẹp Menu (context) đi kèm với tin nhắn khách
prompt_template = ChatPromptTemplate.from_messages([
    ("system", SYSTEM_PROMPT),
    ("user", "MENU ĐƯỢC CUNG CẤP:\n{context}\n\n---\nTIN NHẮN KHÁCH HÀNG:\n{user_text}")
])

chain = prompt_template | llm

# --- LOGIC XỬ LÝ ---
def extract_order_data(user_text: str) -> ExtractedOrderResult:
    try:
        context = ""

        # Tìm 5 món liên quan nhất từ Qdrant nếu retriever sẵn sàng.
        if retriever is not None:
            relevant_docs = retriever.invoke(user_text)
            context = "\n".join([doc.page_content for doc in relevant_docs])

        # Gọi Gemini với Prompt chuẩn
        response = chain.invoke({
            "context": context,
            "user_text": user_text
        })

        # Ép kiểu và Parse JSON
        content = response.content.strip().replace("```json", "").replace("```", "")
        data = json.loads(content)

        return ExtractedOrderResult(**data)

    except Exception as e:
        error_text = str(e)
        print(f"Lỗi Extract Data: {error_text}")

        if "RESOURCE_EXHAUSTED" in error_text or "429" in error_text:
            raise AIQuotaExceededError(error_text) from e

        return ExtractedOrderResult(intent="unknown")

def reload_menu():
  global vector_db, retriever
  vector_db, retriever = initialize_retriever_safely()
  return retriever is not None