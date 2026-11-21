from pydantic_settings import BaseSettings, SettingsConfigDict

# Matches docker-compose.yml's laptop-only database. Deployed environments must set DATABASE_URL.
LOCAL_DATABASE_URL = "postgresql+psycopg://detection:detection@127.0.0.1:5432/detection"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Detection Platform API"
    database_url: str = LOCAL_DATABASE_URL
    database_connect_timeout_seconds: int = 5

    # Outbound feed / article fetching (app/sources/fetch.py). A generic agent string, no personal details.
    fetch_timeout_seconds: float = 15
    fetch_user_agent: str = "DetectionPlatform/0.1 (threat intelligence feed reader)"
    fetch_max_bytes: int = 20_000_000  # full-content feeds (e.g. Elastic Security Labs) exceed 5 MB
