from sqlalchemy import Column, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)

    product_name = Column(String(255), nullable=False)
    size = Column(String(10), nullable=True)
    toppings_json = Column(Text, nullable=True)

    unit_price = Column(Integer, nullable=False, default=0)
    quantity = Column(Integer, nullable=False, default=1)
    line_total = Column(Integer, nullable=False, default=0)

    order = relationship("Order", back_populates="items")