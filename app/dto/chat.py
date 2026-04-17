from typing import Optional
from pydantic import BaseModel, Field


class TelegramMessageData(BaseModel):
    chat_id: str
    user_id: str
    text: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class ExtractedOrderItem(BaseModel):
    item_id: Optional[str] = None
    product_name: Optional[str] = None
    size: Optional[str] = None
    quantity: int = Field(default=1, ge=1)


class ExtractedOrderResult(BaseModel):
    intent: str
    items: list[ExtractedOrderItem] = Field(default_factory=list)
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    delivery_address: Optional[str] = None
    note: Optional[str] = None
    confirmation: Optional[bool] = None