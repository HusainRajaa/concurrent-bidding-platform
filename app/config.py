import os
if "DATABASE_URL" in os.environ: os.environ["DATABASE_URL"] = os.environ["DATABASE_URL"].replace("postgres://", "postgresql+asyncpg://", 1) if os.environ["DATABASE_URL"].startswith("postgres://") else (os.environ["DATABASE_URL"].replace("postgresql://", "postgresql+asyncpg://", 1) if os.environ["DATABASE_URL"].startswith("postgresql://") else os.environ["DATABASE_URL"])

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+asyncpg://nexbid_user:nexbid_password@localhost:5432/nexbid_db"
    REDIS_URL: str = "redis://localhost:6379/0"
    JWT_SECRET_KEY: str = "nexbid_super_secret_signing_key_for_jwt_tokens_replace_in_prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = "noreply@nexbid.com"
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = "http://localhost:8000/auth/google/callback"
    # Resend API Configuration
    RESEND_API_KEY: str = ""

    # Brevo (Sendinblue) Configuration
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = ""
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()

import os
# Support JWT_SECRET env var fallback for JWT_SECRET_KEY
if not os.environ.get("JWT_SECRET_KEY") and os.environ.get("JWT_SECRET"):
    settings.JWT_SECRET_KEY = os.environ.get("JWT_SECRET")

