import httpx

from app.core.config import settings
from app.dto.chat import TelegramMessageData


TELEGRAM_API_URL = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


async def send_message(chat_id: str, text: str) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
            },
        )
        response.raise_for_status()
        return response.json()


async def send_message_to_owner(text: str) -> dict:
    if not settings.TELEGRAM_OWNER_CHAT_ID:
        raise ValueError("TELEGRAM_OWNER_CHAT_ID is not configured")

    return await send_message(settings.TELEGRAM_OWNER_CHAT_ID, text)


def extract_message_data(update: dict) -> TelegramMessageData | None:
    message = update.get("message")
    if not message:
        return None

    chat = message.get("chat", {})
    from_user = message.get("from", {})

    return TelegramMessageData(
        chat_id=str(chat.get("id")),
        user_id=str(from_user.get("id")),
        text=(message.get("text") or "").strip(),
        username=from_user.get("username"),
        first_name=from_user.get("first_name"),
        last_name=from_user.get("last_name"),
    )