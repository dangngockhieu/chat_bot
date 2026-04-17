from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship

from app.core.database import Base


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)

    provider = Column(String(50), nullable=False, default="payos")
    amount = Column(Integer, nullable=False, default=0)
    status = Column(String(50), nullable=False, default="pending")

    payment_link_id = Column(String(255), nullable=True, index=True)
    checkout_url = Column(Text, nullable=True)
    qr_code = Column(Text, nullable=True)
    provider_transaction_id = Column(String(255), nullable=True)
    raw_response_json = Column(Text, nullable=True)

    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    order = relationship("Order", back_populates="payments")