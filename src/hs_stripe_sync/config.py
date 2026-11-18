"""Settings loaded from environment variables (and an optional .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    """Minimal .env loader: KEY=VALUE lines, no overriding of real env vars."""
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    hubspot_token: str
    hubspot_client_secret: str
    stripe_api_key: str
    stripe_webhook_secret: str
    public_base_url: str
    admin_token: str
    db_path: str = "sync.db"
    slack_webhook_url: str | None = None
    echo_window_seconds: int = 120

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            hubspot_token=_required("HUBSPOT_TOKEN"),
            hubspot_client_secret=_required("HUBSPOT_CLIENT_SECRET"),
            stripe_api_key=_required("STRIPE_API_KEY"),
            stripe_webhook_secret=_required("STRIPE_WEBHOOK_SECRET"),
            public_base_url=_required("PUBLIC_BASE_URL"),
            admin_token=_required("ADMIN_TOKEN"),
            db_path=os.environ.get("DB_PATH", "sync.db"),
            slack_webhook_url=os.environ.get("SLACK_WEBHOOK_URL") or None,
            echo_window_seconds=int(os.environ.get("ECHO_WINDOW_SECONDS", "120")),
        )
