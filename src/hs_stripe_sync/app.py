"""FastAPI app: verified webhook endpoints + dead-letter admin endpoints."""
from __future__ import annotations

import hmac
import json
import logging

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Request

from .alerts import LogAlerter, MultiAlerter, SlackAlerter
from .config import Settings
from .hubspot import HttpHubSpotClient
from .signatures import SignatureError, verify_hubspot_v3, verify_stripe
from .store import Store
from .stripe_api import HttpStripeClient
from .sync import SyncEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def build_engine(settings: Settings) -> SyncEngine:
    alerter = LogAlerter()
    if settings.slack_webhook_url:
        alerter = MultiAlerter(alerter, SlackAlerter(settings.slack_webhook_url))
    return SyncEngine(
        HttpHubSpotClient(settings.hubspot_token),
        HttpStripeClient(settings.stripe_api_key),
        Store(settings.db_path),
        alerter,
        echo_window_seconds=settings.echo_window_seconds,
    )


def create_app(settings: Settings | None = None, engine: SyncEngine | None = None, store: Store | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    if engine is None:
        engine = build_engine(settings)
    dead_letter_store = store or engine._store  # noqa: SLF001 - same process, shared store

    app = FastAPI(title="HubSpot <-> Stripe sync", version="0.1.0")

    def require_admin(authorization: str | None = Header(default=None)) -> None:
        expected = f"Bearer {settings.admin_token}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="unauthorized")

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/webhooks/stripe")
    async def stripe_webhook(request: Request, background: BackgroundTasks) -> dict:
        body = await request.body()
        try:
            verify_stripe(body, request.headers.get("stripe-signature"), settings.stripe_webhook_secret)
        except SignatureError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        event = json.loads(body)
        # Acknowledge fast; Stripe retries if we take too long.
        background.add_task(engine.handle_stripe_event, event)
        return {"received": True}

    @app.post("/webhooks/hubspot")
    async def hubspot_webhook(request: Request, background: BackgroundTasks) -> dict:
        body = await request.body()
        public_url = settings.public_base_url.rstrip("/") + request.url.path
        if request.url.query:
            public_url += "?" + request.url.query
        try:
            verify_hubspot_v3(
                "POST",
                public_url,
                body,
                request.headers.get("x-hubspot-signature-v3"),
                request.headers.get("x-hubspot-request-timestamp"),
                settings.hubspot_client_secret,
            )
        except SignatureError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        events = json.loads(body)
        background.add_task(engine.handle_hubspot_events, events if isinstance(events, list) else [events])
        return {"received": True}

    @app.get("/dead-letters", dependencies=[Depends(require_admin)])
    def list_dead_letters() -> list[dict]:
        return [
            {"id": d.id, "source": d.source, "event_id": d.event_id, "error": d.error, "created_at": d.created_at}
            for d in dead_letter_store.open_dead_letters()
        ]

    @app.post("/dead-letters/{dl_id}/replay", dependencies=[Depends(require_admin)])
    def replay(dl_id: int) -> dict:
        try:
            return {"id": dl_id, "outcome": engine.replay_dead_letter(dl_id)}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="dead letter not found") from exc

    return app
