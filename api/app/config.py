from pydantic_settings import BaseSettings, SettingsConfigDict

# Matches docker-compose.yml's laptop-only database. Deployed environments must set DATABASE_URL.
LOCAL_DATABASE_URL = "postgresql+psycopg://detection:detection@127.0.0.1:5432/detection"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Detection Platform API"
    database_url: str = LOCAL_DATABASE_URL
    database_connect_timeout_seconds: int = 5
