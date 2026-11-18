"""Sync engine: applies Stripe events to HubSpot and HubSpot changes to Stripe.

Reliability guarantees:
- Idempotent: each provider event is processed at most once (re-deliveries are skipped).
- Loop-safe: changes we wrote ourselves are recognised and not synced back ("echo guard").
- Order-tolerant: a subscription/invoice event for an unknown customer creates the contact first.
- Resilient: 429/5xx/network errors are retried with backoff; anything still failing is
  dead-lettered, alerted, and can be replayed later.
"""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

from .alerts import Alerter
from .hubspot import HubSpotClient
from .mapping import (
    HUBSPOT_NAME_FIELDS,
    HUBSPOT_TO_STRIPE_FIELDS,
    SYNCED_HUBSPOT_PROPERTIES,
    customer_to_contact_properties,
    invoice_to_contact_properties,
    join_name,
    subscription_to_contact_properties,
)
from .retry import with_retry
from .store import Store
from .stripe_api import StripeClient

log = logging.getLogger("hs_stripe_sync")
T = TypeVar("T")

STRIPE_IDENTITY_FIELDS = ("email", "name", "phone")


class SyncEngine:
    def __init__(
        self,
        hubspot: HubSpotClient,
        stripe: StripeClient,
        store: Store,
        alerter: Alerter,
        *,
        echo_window_seconds: int = 120,
        retry_attempts: int = 5,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._hubspot = hubspot
        self._stripe = stripe
        self._store = store
        self._alerter = alerter
        self._echo_window = echo_window_seconds
        self._attempts = retry_attempts
        self._sleep = sleep

    # ------------------------------------------------------------------ utils
    def _retry(self, fn: Callable[[], T]) -> T:
        def on_retry(attempt: int, exc: Exception, delay: float) -> None:
            log.warning("transient error (attempt %d), retrying in %.1fs: %s", attempt, delay, exc)

        return with_retry(fn, attempts=self._attempts, sleep=self._sleep, on_retry=on_retry)

    def _run(self, source: str, event_id: str, payload: dict, label: str, work: Callable[[], str]) -> str:
        if not self._store.claim_event(source, event_id):
            log.info("%s %s: duplicate delivery skipped", source, event_id)
            return "duplicate"
        try:
            outcome = work()
        except Exception as exc:  # noqa: BLE001 - every failure must be captured
            self._store.finish_event(source, event_id, ok=False)
            dl_id = self._store.add_dead_letter(source, event_id, payload, f"{type(exc).__name__}: {exc}")
            log.exception("%s %s (%s) failed -> dead letter #%d", source, event_id, label, dl_id)
            self._alerter.send(f"Sync failed: {source} {label} (dead letter #{dl_id})", str(exc))
            return "dead_lettered"
        self._store.finish_event(source, event_id, ok=True)
        log.info("%s %s (%s): %s", source, event_id, label, outcome)
        return outcome

    def _write_hubspot(self, contact_id: str, props: dict[str, str]) -> None:
        self._retry(lambda: self._hubspot.update_contact(contact_id, props))
        for key, value in props.items():
            self._store.record_write("hubspot", contact_id, key, value)

    # -------------------------------------------------------- Stripe -> HubSpot
    def handle_stripe_event(self, event: dict) -> str:
        return self._run("stripe", event["id"], event, event.get("type", "?"), lambda: self._apply_stripe(event))

    def _apply_stripe(self, event: dict) -> str:
        etype = event["type"]
        obj = event["data"]["object"]

        if etype in ("customer.created", "customer.updated"):
            if etype == "customer.updated":
                changed = event["data"].get("previous_attributes")
                if changed is not None:
                    relevant = [f for f in STRIPE_IDENTITY_FIELDS if f in changed]
                    if not relevant:
                        return "no_synced_field_changed"
                    if all(
                        self._store.consume_echo("stripe", obj["id"], f, obj.get(f) or "", self._echo_window)
                        for f in relevant
                    ):
                        return "echo_suppressed"
            self._upsert_contact(obj)
            return "contact_upserted"

        if etype.startswith("customer.subscription."):
            contact_id = self._contact_for_customer(obj["customer"])
            self._write_hubspot(contact_id, subscription_to_contact_properties(obj))
            return "subscription_synced"

        if etype == "invoice.paid":
            contact_id = self._contact_for_customer(obj["customer"])
            current = self._retry(lambda: self._hubspot.get_contact(contact_id, ["stripe_lifetime_value"]))
            previous = float((current.get("properties") or {}).get("stripe_lifetime_value") or 0)
            self._write_hubspot(contact_id, invoice_to_contact_properties(obj, previous))
            return "payment_synced"

        return "ignored"

    def _contact_for_customer(self, customer_id: str) -> str:
        linked = self._store.hubspot_id_for(customer_id)
        if linked:
            return linked
        # Event arrived before (or without) customer.created: fetch and create now.
        customer = self._retry(lambda: self._stripe.get_customer(customer_id))
        return self._upsert_contact(customer)

    def _upsert_contact(self, customer: dict) -> str:
        props = customer_to_contact_properties(customer)
        contact_id = self._store.hubspot_id_for(customer["id"])
        if not contact_id and props.get("email"):
            existing = self._retry(lambda: self._hubspot.find_contact_by_email(props["email"]))
            contact_id = existing["id"] if existing else None

        if contact_id:
            self._write_hubspot(contact_id, props)
        else:
            contact_id = self._retry(lambda: self._hubspot.create_contact(props))
            for key, value in props.items():
                self._store.record_write("hubspot", contact_id, key, value)
        self._store.link(customer["id"], contact_id)

        # Back-link on the Stripe side so support staff can jump to the CRM record.
        if (customer.get("metadata") or {}).get("hubspot_contact_id") != contact_id:
            self._retry(lambda: self._stripe.update_customer(
                customer["id"], {}, metadata={"hubspot_contact_id": contact_id}
            ))
        return contact_id

    # -------------------------------------------------------- HubSpot -> Stripe
    def handle_hubspot_events(self, events: list[dict]) -> list[str]:
        return [self.handle_hubspot_event(e) for e in events]

    def handle_hubspot_event(self, event: dict) -> str:
        if (
            event.get("subscriptionType") != "contact.propertyChange"
            or event.get("propertyName") not in SYNCED_HUBSPOT_PROPERTIES
        ):
            return "ignored"
        label = f"contact.propertyChange:{event['propertyName']}"
        return self._run("hubspot", str(event["eventId"]), event, label, lambda: self._apply_hubspot(event))

    def _apply_hubspot(self, event: dict) -> str:
        contact_id = str(event["objectId"])
        prop = event["propertyName"]
        value = event.get("propertyValue") or ""

        if self._store.consume_echo("hubspot", contact_id, prop, value, self._echo_window):
            return "echo_suppressed"

        customer_id = self._store.stripe_id_for(contact_id)
        if not customer_id:
            contact = self._retry(lambda: self._hubspot.get_contact(contact_id, ["stripe_customer_id"]))
            customer_id = (contact.get("properties") or {}).get("stripe_customer_id")
            if not customer_id:
                return "not_linked"
            self._store.link(customer_id, contact_id)

        if prop in HUBSPOT_NAME_FIELDS:
            contact = self._retry(lambda: self._hubspot.get_contact(contact_id, ["firstname", "lastname"]))
            props = contact.get("properties") or {}
            field, new_value = "name", join_name(props.get("firstname"), props.get("lastname"))
        else:
            field, new_value = HUBSPOT_TO_STRIPE_FIELDS[prop], value

        self._store.record_write("stripe", customer_id, field, new_value)
        self._retry(lambda: self._stripe.update_customer(customer_id, {field: new_value}))
        return "stripe_updated"

    # ------------------------------------------------------------ dead letters
    def replay_dead_letter(self, dl_id: int) -> str:
        dead = self._store.get_dead_letter(dl_id)
        if dead is None:
            raise KeyError(dl_id)
        self._store.resolve_dead_letter(dl_id)  # a new dead letter is created if it fails again
        if dead.source == "stripe":
            return self.handle_stripe_event(dead.payload)
        return self.handle_hubspot_event(dead.payload)
