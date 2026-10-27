"""Application configuration, sourced from the environment only."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from flowstate.domain.enums import ActorKind, ActorRole

REPO_ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "dev", "test", "local"})
DEFAULT_SECRET_KEY = "development-only-key-change-me"


class Settings(BaseSettings):
    """Runtime settings.

    Every value can be overridden with a ``FLOWSTATE_`` prefixed environment
    variable. Development-only secret defaults are rejected in deployed modes.
    """

    model_config = SettingsConfigDict(
        env_prefix="FLOWSTATE_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FLOWSTATE AI"
    environment: str = "development"
    api_prefix: str = "/api/v1"
    log_level: str = "INFO"
    log_json: bool = True

    data_dir: Path = REPO_ROOT / ".flowstate"
    database_url: str = ""

    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    # Open API access is permitted only in an explicitly local development mode.
    api_token: str | None = None
    secret_key: str = DEFAULT_SECRET_KEY
    api_actor_kind: ActorKind = ActorKind.AGENT
    api_actor_role: ActorRole = ActorRole.OPERATOR
    demo_seed_enabled: bool = True
    auto_seed_demo: bool = True
    decision_ttl_seconds: int = 600
    run_action_budget: int = 64

    # Controlled demo app used by workflow learning. Only this origin may be
    # recorded or replayed. Override it when the API runs on another port.
    demo_app_url: str = "http://127.0.0.1:8000/demo-app/"
    capture_enabled: bool = True
    capture_max_events: int = 80
    browser_timeout_ms: int = 8000

    @property
    def demo_origin(self) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(self.demo_app_url)
        return f"{parsed.scheme}://{parsed.netloc}".lower()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("api_token", mode="before")
    @classmethod
    def _empty_token_means_disabled(cls, value: object) -> object:
        # An empty environment variable is a common way to "unset" a token.
        # Treat it as absent rather than enabling bearer auth with "".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _validate_non_development_security(self) -> Settings:
        if self.environment.strip().lower() in DEVELOPMENT_ENVIRONMENTS:
            return self

        errors: list[str] = []
        if (
            self.api_token is None
            or len(self.api_token.strip()) < 32
            or self.api_token != self.api_token.strip()
        ):
            errors.append("FLOWSTATE_API_TOKEN must contain at least 32 characters")
        if self.secret_key == DEFAULT_SECRET_KEY or len(self.secret_key.strip()) < 32:
            errors.append("FLOWSTATE_SECRET_KEY must be explicitly set to at least 32 characters")
        if any(origin == "*" or self._is_loopback_origin(origin) for origin in self.cors_origins):
            errors.append("FLOWSTATE_CORS_ORIGINS must not contain a wildcard or loopback origin")
        if self.auto_seed_demo or self.demo_seed_enabled or self.capture_enabled:
            errors.append(
                "demo seeding and browser capture must be disabled outside local development"
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self

    @staticmethod
    def _is_loopback_origin(origin: str) -> bool:
        from urllib.parse import urlparse

        return (urlparse(origin).hostname or "").lower() in {
            "localhost",
            "127.0.0.1",
            "::1",
        }

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'flowstate.db').as_posix()}"

    @property
    def auth_mode(self) -> str:
        return "bearer" if self.api_token else "open-development"


@lru_cache
def get_settings() -> Settings:
    return Settings()
