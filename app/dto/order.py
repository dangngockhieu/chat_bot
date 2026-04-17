from typing import Optional
from pydantic import BaseModel, Field


class OrderItemResponse(BaseModel):
    id: int
    product_name: str
    size: Optional[str] = None
    toppings_json: Optional[str] = None
    unit_price: int
    quantity: int
    line_total: int

    model_config = {"from_attributes": True}


class AddOrderItemRequest(BaseModel):
    product_name: str
    size: Optional[str] = None
    unit_price: int = Field(ge=0)
    quantity: int = Field(default=1, ge=1)
    toppings: list[dict] = Field(default_factory=list)


class UpdateCustomerInfoRequest(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    delivery_address: Optional[str] = None
    note: Optional[str] = None


class OrderResponse(BaseModel):
    id: int
    order_code: str
    platform: str
    platform_user_id: str
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    delivery_address: Optional[str] = None
    note: Optional[str] = None
    status: str
    subtotal_amount: int
    total_amount: int
    items: list[OrderItemResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}