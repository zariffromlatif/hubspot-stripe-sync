"""Failure alerts: log always, Slack when a webhook URL is configured."""
from __future__ import annotations

import logging
from typing import Protocol

import httpx

log = logging.getLogger("hs_stripe_sync.alerts")


class Alerter(Protocol):
    def send(self, title: str, detail: str) -> None: ...


class LogAlerter:
    def send(self, title: str, detail: str) -> None:
        log.error("ALERT: %s | %s", title, detail)


class SlackAlerter:
    def __init__(self, webhook_url: str, http: httpx.Client | None = None):
        self._url = webhook_url
        self._http = http or httpx.Client(timeout=10.0)

    def send(self, title: str, detail: str) -> None:
        try:
            self._http.post(self._url, json={"text": f":rotating_light: *{title}*\n```{detail[:2500]}```"})
        except httpx.HTTPError:
            log.exception("Slack alert failed")  # never let alerting break the sync


class MultiAlerter:
    def __init__(self, *alerters: Alerter):
        self._alerters = alerters

    def send(self, title: str, detail: str) -> None:
        for alerter in self._alerters:
            alerter.send(title, detail)


class MemoryAlerter:
    """Collects alerts in memory (tests and demo)."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, detail: str) -> None:
        self.sent.append((title, detail))
