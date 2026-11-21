from typing import Literal

from pydantic import SecretStr
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

    # LLM (app/llm/factory.py). Only the worker builds a client; the API never calls a model.
    llm_provider: Literal["anthropic", "offline"] = "anthropic"
    anthropic_api_key: SecretStr | None = None  # from ANTHROPIC_API_KEY; SecretStr keeps it out of reprs and logs
    llm_timeout_seconds: float = 300  # Opus extraction with thinking can take minutes
    relevance_model: str = "claude-haiku-5-5"
    relevance_effort: Literal["low", "medium", "high"] = "low"
    extraction_model: str = "claude-opus-5-5"
    extraction_effort: Literal["low", "medium", "high"] = "medium"
    rules_model: str = "claude-opus-5-5"
    rules_effort: Literal["low", "medium", "high"] = "medium"
    max_rule_groups_per_run: int = 6  # bounds Opus calls per article

    worker_poll_seconds: float = 2
    # Most articles the priority gate may auto-queue per UTC day (highest scores first); 0 turns auto-queueing off.
    auto_queue_max_per_day: int = 25
    # A running run with no progress for this long is failed so it can be re-queued (must exceed one LLM call: timeout x (retries + 1)).
    run_stale_after_seconds: int = 1800
