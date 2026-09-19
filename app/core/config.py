from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    APP_NAME: str = "Python AI Bot"
    API_V1_PREFIX: str = "/api"
    DEBUG: bool = False
    CORS_ORIGINS: list[str] = ["*"]

    # Database (XAMPP MySQL by default; any SQLAlchemy URL works)
    DATABASE_URL: str = "mysql+pymysql://root:@127.0.0.1:3306/python_ai_bot"

    # Assignment portal (read-only; the bot never writes here)
    PORTAL_DATABASE_URL: str = "mysql+pymysql://root:@127.0.0.1:3306/assignmentportaluat"
    # A runaway portal query must fail rather than hold up a chat reply.
    # Seconds; 0 disables the cap.
    PORTAL_STATEMENT_TIMEOUT_SECONDS: float = 15.0

    # Auth
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24

    # AI provider (Google Gemini)
    GEMINI_API_KEY: str = ""
    AI_MODEL: str = "gemini-2.5-flash"
    AI_SYSTEM_PROMPT: str = "You are a helpful assistant."
    # Whether Gemini may query the assignment portal via tools
    AI_TOOLS_ENABLED: bool = True
    # Gemini counts *thinking* tokens against this budget, not just the visible
    # answer -- measured at ~280 thinking tokens for a trivial 12-row table, and
    # far more for a real lookup. 1024 truncated any answer with a table in it
    # mid-row, reported as finish_reason=MAX_TOKENS.
    AI_MAX_TOKENS: int = 8192
    # None leaves the model's own thinking behaviour alone; 0 turns it off and
    # hands the whole budget to the answer.
    AI_THINKING_BUDGET: int | None = None
    AI_TEMPERATURE: float = 0.7

    # How many past messages to replay as context on each turn
    HISTORY_LIMIT: int = 20


@lru_cache
def get_settings() -> Settings:
    """Cached so the .env file is parsed once per process."""
    return Settings()


settings = get_settings()
