from typing import Optional

from pydantic import BaseModel


class CreatePaymentResponse(BaseModel):
    payment_link_id: Optional[str] = None
    checkout_url: Optional[str] = None
    qr_code: Optional[str] = None
    amount: int
    status: str


class PayOSWebhookData(BaseModel):
    orderCode: Optional[int] = None
    amount: Optional[int] = None
    description: Optional[str] = None
    accountNumber: Optional[str] = None
    reference: Optional[str] = None
    transactionDateTime: Optional[str] = None
    currency: Optional[str] = None
    paymentLinkId: Optional[str] = None
    code: Optional[str] = None
    desc: Optional[str] = None
    counterAccountBankId: Optional[str] = None
    counterAccountBankName: Optional[str] = None
    counterAccountName: Optional[str] = None
    counterAccountNumber: Optional[str] = None
    virtualAccountName: Optional[str] = None
    virtualAccountNumber: Optional[str] = None


class PayOSWebhookPayload(BaseModel):
    code: str
    desc: str
    success: bool
    data: Optional[PayOSWebhookData] = None
    signature: Optional[str] = None