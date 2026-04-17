import json

from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.order_item import OrderItem
from app.dto.order import AddOrderItemRequest, UpdateCustomerInfoRequest


def generate_order_code(order_id: int) -> str:
    return f"ORD{order_id:06d}"


def get_or_create_draft_order(
    db: Session,
    platform_user_id: str,
    platform: str = "telegram",
) -> Order:
    order = (
        db.query(Order)
        .filter(
            Order.platform_user_id == platform_user_id,
            Order.platform == platform,
            Order.status == "draft",
        )
        .order_by(Order.id.desc())
        .first()
    )

    if order:
        return order

    order = Order(
        order_code="TEMP",
        platform=platform,
        platform_user_id=platform_user_id,
        status="draft",
        subtotal_amount=0,
        total_amount=0,
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    order.order_code = generate_order_code(order.id)
    db.add(order)
    db.commit()
    db.refresh(order)

    return order


def recalculate_order_total(db: Session, order: Order) -> Order:
    total = sum(item.line_total for item in order.items)
    order.subtotal_amount = total
    order.total_amount = total

    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def add_item_to_order(
    db: Session,
    order: Order,
    payload: AddOrderItemRequest,
) -> OrderItem:
    toppings_total = sum(int(t.get("price", 0)) for t in payload.toppings)
    final_unit_price = payload.unit_price + toppings_total
    line_total = final_unit_price * payload.quantity

    order_item = OrderItem(
        order_id=order.id,
        product_name=payload.product_name,
        size=payload.size,
        toppings_json=json.dumps(payload.toppings, ensure_ascii=False),
        unit_price=final_unit_price,
        quantity=payload.quantity,
        line_total=line_total,
    )

    db.add(order_item)
    db.commit()
    db.refresh(order_item)
    db.refresh(order)

    recalculate_order_total(db, order)
    return order_item


def update_order_customer_info(
    db: Session,
    order: Order,
    payload: UpdateCustomerInfoRequest,
) -> Order:
    if payload.customer_name is not None:
        order.customer_name = payload.customer_name
    if payload.customer_phone is not None:
        order.customer_phone = payload.customer_phone
    if payload.delivery_address is not None:
        order.delivery_address = payload.delivery_address
    if payload.note is not None:
        order.note = payload.note

    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def is_order_ready_for_payment(order: Order) -> bool:
    return bool(
        order.customer_name
        and order.customer_phone
        and order.delivery_address
        and len(order.items) > 0
        and order.total_amount > 0
    )