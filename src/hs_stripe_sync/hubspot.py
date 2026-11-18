"""Thin HubSpot CRM v3 client (contacts + custom properties)."""
from __future__ import annotations

from typing import Any, Protocol

import httpx

from .http_errors import request
from .retry import PermanentError

HUBSPOT_API = "https://api.hubapi.com"

# Custom contact properties this integration owns.
PROPERTY_GROUP = {"name": "stripe_sync", "label": "Stripe (synced)"}
CUSTOM_PROPERTIES: list[dict[str, Any]] = [
    {"name": "stripe_customer_id", "label": "Stripe customer ID", "type": "string", "fieldType": "text"},
    {"name": "stripe_subscription_status", "label": "Stripe subscription status", "type": "string", "fieldType": "text"},
    {"name": "stripe_mrr", "label": "Stripe MRR", "type": "number", "fieldType": "number"},
    {"name": "stripe_lifetime_value", "label": "Stripe lifetime value", "type": "number", "fieldType": "number"},
    {"name": "stripe_last_payment_date", "label": "Stripe last payment date", "type": "date", "fieldType": "date"},
]


class HubSpotClient(Protocol):
    def find_contact_by_email(self, email: str) -> dict | None: ...
    def get_contact(self, contact_id: str, properties: list[str]) -> dict: ...
    def create_contact(self, properties: dict[str, str]) -> str: ...
    def update_contact(self, contact_id: str, properties: dict[str, str]) -> None: ...


class HttpHubSpotClient:
    def __init__(self, token: str, http: httpx.Client | None = None, base_url: str = HUBSPOT_API):
        self._http = http or httpx.Client(timeout=20.0)
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        return request(self._http, "HubSpot", method, f"{self._base}{path}", headers=self._headers, **kwargs)

    def find_contact_by_email(self, email: str) -> dict | None:
        body = {
            "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}],
            "properties": ["email", "firstname", "lastname", "phone", "stripe_customer_id"],
            "limit": 1,
        }
        results = self._call("POST", "/crm/v3/objects/contacts/search", json=body).json().get("results", [])
        return results[0] if results else None

    def get_contact(self, contact_id: str, properties: list[str]) -> dict:
        params = {"properties": ",".join(properties)}
        return self._call("GET", f"/crm/v3/objects/contacts/{contact_id}", params=params).json()

    def create_contact(self, properties: dict[str, str]) -> str:
        return self._call("POST", "/crm/v3/objects/contacts", json={"properties": properties}).json()["id"]

    def update_contact(self, contact_id: str, properties: dict[str, str]) -> None:
        self._call("PATCH", f"/crm/v3/objects/contacts/{contact_id}", json={"properties": properties})

    # --- one-time setup ---------------------------------------------------
    def ensure_custom_properties(self) -> list[str]:
        """Create the property group and properties. Safe to re-run."""
        created: list[str] = []
        try:
            self._call("POST", "/crm/v3/properties/contacts/groups", json=PROPERTY_GROUP)
            created.append(f"group:{PROPERTY_GROUP['name']}")
        except PermanentError as exc:
            if "409" not in str(exc):
                raise
        for prop in CUSTOM_PROPERTIES:
            try:
                self._call(
                    "POST",
                    "/crm/v3/properties/contacts",
                    json={**prop, "groupName": PROPERTY_GROUP["name"]},
                )
                created.append(prop["name"])
            except PermanentError as exc:
                if "409" not in str(exc):
                    raise
        return created
