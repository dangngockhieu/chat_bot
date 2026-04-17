from fastapi import FastAPI

from app.core.database import Base, engine

app = FastAPI(title="Milk Tea Telegram Bot")

Base.metadata.create_all(bind=engine)


@app.get("/")
def root():
    return {"message": "Milk Tea Bot is running"}