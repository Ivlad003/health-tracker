from pydantic import field_validator
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

    # Apple Health
    apple_health_sync_hours: int = 6

    # OpenAI
    openai_api_key: str = ""
    openai_model: str = "gpt-4o"

    # New Relic
    new_relic_license_key: str = ""

    # App
    app_base_url: str = "http://localhost:8000"
    log_level: str = "INFO"
    # Default timezone for users whose `users.timezone` is empty/invalid and
    # for global cron jobs (briefings).
    default_timezone: str = "Europe/Kyiv"

    # Security
    # Secret used to HMAC-sign OAuth `state` values. When empty, a key is
    # derived from WHOOP/FatSecret client secrets (stable across restarts).
    oauth_state_secret: str = ""
    # Bearer token for /debug/* endpoints. When empty, debug endpoints are
    # disabled entirely (404).
    admin_api_token: str = ""

    # Outbound HTTP
    http_timeout_seconds: float = 15.0

    # Food logging (plan 2026-09-26). Feature defaults; admins can switch the
    # runtime flags off/on only where the capability is available.
    food_history_enabled: bool = True
    food_barcode_enabled: bool = True
    food_vision_enabled: bool = True
    food_plate_photos_enabled: bool = False
    # FatSecret Premier barcode add-on (OAuth2 scope `barcode`). Only enable
    # when the account is entitled; a flag cannot grant the entitlement.
    fatsecret_barcode_enabled: bool = False
    fatsecret_history_import_days: int = 30
    # FatSecret storable-data policy: non-ID data cached at most 24 h.
    fatsecret_cache_hours: int = 24
    openai_vision_model: str = "gpt-4o"
    vision_max_concurrency: int = 2
    media_max_bytes: int = 10 * 1024 * 1024
    media_max_pixels: int = 40_000_000
    draft_ttl_hours: int = 24
    off_base_url: str = "https://world.openfoodfacts.org"
    # Pinned API version: 3.5+ changes the nutrition structure (still in
    # development upstream); 3.4 serves the documented `nutriments` object.
    off_api_version: str = "3.4"
    off_user_agent: str = "HealthTrackerBot/1.0 (contact: set OFF_USER_AGENT)"
    off_reads_per_minute: int = 15

    # Telegram Web App
    webapp_url: str = ""  # defaults to {app_base_url}/app/
    # Idle timeout: every active request (throttled to once a minute) slides
    # the expiry forward by this much, up to webapp_auth.SESSION_MAX_LIFETIME.
    webapp_session_ttl_seconds: int = 3600
    webapp_auth_max_age_seconds: int = 300
    # Comma-separated Telegram user ids bootstrapped as owner/admin.
    webapp_admin_telegram_ids: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}

    @field_validator("apple_health_sync_hours")
    @classmethod
    def _validate_sync_hours(cls, value: int) -> int:
        # Must satisfy the apple_health_sync.sync_frequency_hours CHECK (1..24).
        if not 1 <= value <= 24:
            raise ValueError("APPLE_HEALTH_SYNC_HOURS must be between 1 and 24")
        return value

    @field_validator("fatsecret_history_import_days")
    @classmethod
    def _validate_import_days(cls, value: int) -> int:
        if not 1 <= value <= 365:
            raise ValueError("FATSECRET_HISTORY_IMPORT_DAYS must be between 1 and 365")
        return value

    @field_validator("fatsecret_cache_hours")
    @classmethod
    def _validate_cache_hours(cls, value: int) -> int:
        # FatSecret's storable-data rules cap non-ID caching at 24 hours.
        if not 1 <= value <= 24:
            raise ValueError("FATSECRET_CACHE_HOURS must be between 1 and 24")
        return value

    @property
    def admin_telegram_ids(self) -> set[int]:
        ids: set[int] = set()
        for part in (self.webapp_admin_telegram_ids or "").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        return ids

    @property
    def effective_webapp_url(self) -> str:
        return self.webapp_url or f"{self.app_base_url.rstrip('/')}/app/"


settings = Settings()
