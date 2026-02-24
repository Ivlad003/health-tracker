from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    database_url: str

    # Telegram
    telegram_bot_token: str = ""

    # WHOOP
    whoop_client_id: str
    whoop_client_secret: str
    whoop_redirect_uri: str

    # FatSecret
    fatsecret_client_id: str
    fatsecret_client_secret: str
    fatsecret_shared_secret: str = ""

    # OpenAI
    openai_api_key: str = ""

    # App
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
