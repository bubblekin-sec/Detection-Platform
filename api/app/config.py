from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
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

    # Where `cli sync-rules` writes approved rules: the repo's rules/ when run from api/; at enterprise, a checkout of
    # the rules repo (or a mounted share) in the job that runs the sync. The API and worker never write here.
    rules_dir: Path = Path("../rules")
    # Folders of Sigma rules that generated rules are cross-referenced against (`cli crossref-rules`), comma-separated.
    # Empty means rules_dir (our own approved rules); at enterprise, a checkout of the organisation's ruleset.
    reference_rule_dirs: str = ""

    # Defender XDR tables the organisation actually collects, comma-separated. A query reading any other table gets a
    # "not in the telemetry inventory" warning in Review. Empty means the seven Device* event tables the KQL prompt uses.
    telemetry_tables: str = ""

    @field_validator("telemetry_tables")
    @classmethod
    def known_tables(cls, value: str) -> str:
        from app.detection.kql_schema import TABLES  # lazy: pulls in the Kusto backend

        unknown = [name for name in _split(value) if name not in TABLES]
        if unknown:
            raise ValueError(f"not Defender XDR tables: {', '.join(unknown)}")
        return value

    @property
    def telemetry_inventory(self) -> frozenset[str]:
        from app.profiles.compile import KQL_PROMPT_TABLES

        return frozenset(_split(self.telemetry_tables) or KQL_PROMPT_TABLES)

    @property
    def reference_folders(self) -> list[Path]:
        folders = [Path(part.strip()) for part in self.reference_rule_dirs.split(",") if part.strip()]
        return folders or [self.rules_dir]


def _split(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]
