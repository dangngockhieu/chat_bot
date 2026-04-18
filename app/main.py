from fastapi import FastAPI

from app.api.telegram import router as telegram_router
from app.api.payment import router as payment_router
from app.core.database import Base, engine
from app.models import Order, OrderItem, Payment

app = FastAPI(title="Milk Tea Telegram Bot")

Base.metadata.create_all(bind=engine)

app.include_router(telegram_router)
app.include_router(payment_router)


@app.get("/")
def root():
    return {"message": "Milk Tea Bot is running"}