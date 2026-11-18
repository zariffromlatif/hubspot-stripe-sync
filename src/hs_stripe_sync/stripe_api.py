"""Thin Stripe REST client (customers only)."""
from __future__ import annotations

from typing import Protocol

import httpx

from .http_errors import request

STRIPE_API = "https://api.stripe.com"


class StripeClient(Protocol):
    def get_customer(self, customer_id: str) -> dict: ...
    def update_customer(self, customer_id: str, fields: dict[str, str], metadata: dict[str, str] | None = None) -> dict: ...


class HttpStripeClient:
    def __init__(self, api_key: str, http: httpx.Client | None = None, base_url: str = STRIPE_API):
        self._http = http or httpx.Client(timeout=20.0)
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def get_customer(self, customer_id: str) -> dict:
        return request(self._http, "Stripe", "GET", f"{self._base}/v1/customers/{customer_id}", headers=self._headers).json()

    def update_customer(self, customer_id: str, fields: dict[str, str], metadata: dict[str, str] | None = None) -> dict:
        form = dict(fields)
        for key, value in (metadata or {}).items():
            form[f"metadata[{key}]"] = value
        return request(
            self._http, "Stripe", "POST", f"{self._base}/v1/customers/{customer_id}",
            headers=self._headers, data=form,
        ).json()
