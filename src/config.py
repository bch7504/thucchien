from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = "AI20K Agent"
    app_env: Literal["development", "production", "test"] = "development"
    app_port: int = Field(default=8000, ge=1, le=65535)
    app_host: str = "0.0.0.0"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: str = "http://localhost:3000"

    # LLM
    llm_provider: Literal["google", "groq", "openai"] = "google"
    google_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    model_name: str = "gemini-2.5-flash"
    llm_temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    daily_token_budget: int = Field(default=200_000, ge=0)

    # Database — PostgreSQL only, no SQLite fallback. Required: no default, so a missing/misconfigured
    # DATABASE_URL fails fast at startup instead of silently falling back to a file-based DB.
    database_url: str

    # Auth
    secret_key: str = "dev-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    initial_admin_email: str = ""
    # "Sign in with Google" - Web application OAuth Client ID (audience for ID-token verification
    # only, never an authorization-code exchange, so no client secret needed). Distinct from the
    # Calendar OAuth client below - two separate Google Cloud OAuth Clients on purpose, so a user
    # can log in without ever being asked for Calendar access, and vice versa.
    google_oauth_client_id: str = ""

    # Vector Store
    chroma_persist_dir: str = "./data/chroma"

    # Google Calendar - per-user OAuth (each user connects their own Calendar from the Calendar
    # page via a real redirect + backend callback; there is no shared/fallback calendar). This IS
    # an authorization-code exchange (to get a refresh_token we can use outside the browser), so
    # unlike google_oauth_client_id above, this Client needs a secret. Create a separate "Web
    # application" OAuth Client for this in Google Cloud Console - see .env.example. calendarId is
    # always "primary" now (credential is already the user's own), so no google_calendar_id setting.
    google_calendar_client_id: str = ""
    google_calendar_client_secret: str = ""
    google_calendar_redirect_uri: str = "http://localhost:8000/api/v1/calendar/oauth/callback"
    calendar_timezone: str = "Asia/Ho_Chi_Minh"

    # Fernet key encrypting refresh_token/access_token at rest (src/auth/crypto.py) - a Calendar
    # refresh token is a long-lived secret (unlike a password hash, it's directly usable to read/
    # write someone's calendar until they revoke it), so unlike most other secrets in this app it
    # gets encrypted, not just kept out of git. Generate with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Never rotate after users have connected - doing so turns every stored refresh_token into
    # garbage and forces everyone to reconnect.
    credential_encryption_key: str = ""

    # Frontend origin, used as postMessage's targetOrigin on the OAuth callback page so only our
    # own frontend (not an arbitrary embedded/opener page) can receive the "connected" signal.
    frontend_origin: str = "http://localhost:5173"

    # Reminders / scheduler
    scheduler_timezone: str = "Asia/Ho_Chi_Minh"

    # Calendar polling (no public HTTPS URL yet for Google's real push/webhook channels, so
    # changes made directly in Google Calendar are picked up by polling with a syncToken instead)
    calendar_poll_interval_seconds: int = Field(default=20, ge=5)


@lru_cache
def get_settings() -> Settings:
    return Settings()
