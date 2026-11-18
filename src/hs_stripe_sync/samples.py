"""Builders for realistic Stripe / HubSpot webhook payloads (tests + demo)."""
from __future__ import annotations

import itertools
import time

_seq = itertools.count(1)


def stripe_event(event_type: str, obj: dict, previous_attributes: dict | None = None, event_id: str | None = None) -> dict:
    data: dict = {"object": obj}
    if previous_attributes is not None:
        data["previous_attributes"] = previous_attributes
    return {
        "id": event_id or f"evt_{next(_seq):06d}",
        "object": "event",
        "type": event_type,
        "created": int(time.time()),
        "data": data,
    }


def customer(customer_id: str, email: str, name: str, phone: str | None = None) -> dict:
    return {"id": customer_id, "object": "customer", "email": email, "name": name, "phone": phone, "metadata": {}}


def subscription(customer_id: str, status: str, unit_amount: int, interval: str = "month", quantity: int = 1) -> dict:
    return {
        "id": f"sub_{next(_seq):06d}",
        "object": "subscription",
        "customer": customer_id,
        "status": status,
        "items": {"data": [{
            "quantity": quantity,
            "price": {"unit_amount": unit_amount, "currency": "usd",
                      "recurring": {"interval": interval, "interval_count": 1}},
        }]},
    }


def invoice_paid(customer_id: str, amount_paid: int, paid_at: int | None = None) -> dict:
    return {
        "id": f"in_{next(_seq):06d}",
        "object": "invoice",
        "customer": customer_id,
        "amount_paid": amount_paid,
        "currency": "usd",
        "status_transitions": {"paid_at": paid_at or int(time.time())},
    }


def hubspot_property_change(contact_id: str, prop: str, value: str, event_id: int | None = None) -> dict:
    return {
        "eventId": event_id or next(_seq) + 9_000_000,
        "subscriptionType": "contact.propertyChange",
        "objectId": int(contact_id),
        "propertyName": prop,
        "propertyValue": value,
        "changeSource": "CRM_UI",
        "occurredAt": int(time.time() * 1000),
        "attemptNumber": 0,
    }
