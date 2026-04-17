from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    DATABASE_URL: str
    OPENAI_API_KEY: str

    PAYOS_CLIENT_ID: str
    PAYOS_API_KEY: str
    PAYOS_CHECKSUM_KEY: str

    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_BOT_USERNAME: str | None = None
    TELEGRAM_OWNER_CHAT_ID: str

    APP_BASE_URL: str | None = None
    TELEGRAM_WEBHOOK_SECRET: str | None = None

    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )


settings = Settings()