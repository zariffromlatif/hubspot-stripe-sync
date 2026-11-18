"""Offline end-to-end demo: real HTTP endpoints + real signature checks, fake HubSpot/Stripe.

    uv run python demo.py
"""
from __future__ import annotations

import json
import logging
import time

from fastapi.testclient import TestClient

from hs_stripe_sync import samples
from hs_stripe_sync.alerts import MemoryAlerter
from hs_stripe_sync.app import create_app
from hs_stripe_sync.config import Settings
from hs_stripe_sync.fakes import FakeHubSpot, FakeStripe
from hs_stripe_sync.retry import PermanentError, RetryableError
from hs_stripe_sync.signatures import sign_hubspot_v3, sign_stripe
from hs_stripe_sync.store import Store
from hs_stripe_sync.sync import SyncEngine

logging.disable(logging.CRITICAL)  # keep the narration readable

SETTINGS = Settings(
    hubspot_token="demo", hubspot_client_secret="hs_demo_secret", stripe_api_key="sk_test_demo",
    stripe_webhook_secret="whsec_demo", public_base_url="https://sync.agency-client.example", admin_token="demo-admin",
)

hubspot, stripe, alerter = FakeHubSpot(), FakeStripe(), MemoryAlerter()
engine = SyncEngine(hubspot, stripe, Store(":memory:"), alerter, sleep=lambda s: None)
client = TestClient(create_app(SETTINGS, engine=engine))
outcomes: list[str] = []
_original = engine.handle_stripe_event


def _record(event):  # capture what the background worker decided
    outcome = _original(event)
    outcomes.append(outcome)
    return outcome


engine.handle_stripe_event = _record  # type: ignore[method-assign]
_original_hs = engine.handle_hubspot_events
engine.handle_hubspot_events = lambda events: outcomes.extend(_original_hs(events))  # type: ignore[method-assign]


def step(n: int, text: str) -> None:
    print(f"\n[{n}] {text}")


def send_stripe(event: dict, secret: str = "whsec_demo") -> int:
    body = json.dumps(event).encode()
    r = client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": sign_stripe(body, secret)})
    return r.status_code


def send_hubspot(events: list[dict]) -> int:
    body = json.dumps(events).encode()
    ts = str(int(time.time() * 1000))
    sig = sign_hubspot_v3("POST", SETTINGS.public_base_url + "/webhooks/hubspot", body, ts, SETTINGS.hubspot_client_secret)
    r = client.post("/webhooks/hubspot", content=body,
                    headers={"X-HubSpot-Signature-v3": sig, "X-HubSpot-Request-Timestamp": ts})
    return r.status_code


def show(result: str) -> None:
    print(f"    -> {result}")


cust = samples.customer("cus_DEMO01", "maria@brightpath.example", "Maria Gomez", "+1 415 555 0134")
stripe.add_customer(cust)

step(1, "New customer signs up in Stripe -> customer.created webhook")
send_stripe(created := samples.stripe_event("customer.created", cust))
contact_id = next(iter(hubspot.contacts))
show(f"{outcomes[-1]}: HubSpot contact #{contact_id} {hubspot.contacts[contact_id]}")
show(f"Stripe customer back-linked: metadata={stripe.customers['cus_DEMO01']['metadata']}")

step(2, "Stripe re-delivers the same event (it does this on timeouts)")
send_stripe(created)
show(f"{outcomes[-1]}: no duplicate contact (contacts in HubSpot: {len(hubspot.contacts)})")

step(3, "Customer starts a $49/month plan, billed for 3 seats")
send_stripe(samples.stripe_event("customer.subscription.created",
                                 samples.subscription("cus_DEMO01", "active", 4900, quantity=3)))
c = hubspot.contacts[contact_id]
show(f"{outcomes[-1]}: status={c['stripe_subscription_status']}, MRR=${c['stripe_mrr']}")

step(4, "First invoice is paid")
send_stripe(samples.stripe_event("invoice.paid", samples.invoice_paid("cus_DEMO01", 14700)))
c = hubspot.contacts[contact_id]
show(f"{outcomes[-1]}: lifetime value=${c['stripe_lifetime_value']}, last payment={c['stripe_last_payment_date']}")

step(5, "Sales rep corrects the phone number in HubSpot")
hubspot.contacts[contact_id]["phone"] = "+1 415 555 0199"
send_hubspot([samples.hubspot_property_change(contact_id, "phone", "+1 415 555 0199")])
show(f"{outcomes[-1]}: Stripe phone is now {stripe.customers['cus_DEMO01']['phone']}")

step(6, "Stripe fires customer.updated for the change WE just made (classic infinite-loop trap)")
send_stripe(samples.stripe_event("customer.updated", stripe.customers["cus_DEMO01"],
                                 previous_attributes={"phone": "+1 415 555 0134"}))
show(f"{outcomes[-1]}: loop prevented, nothing written back")

step(7, "HubSpot API rate-limits us twice (429, then 503) during a payment sync")
hubspot.fail_next = [RetryableError("HubSpot 429: rate limited", retry_after=1), RetryableError("HubSpot 503")]
send_stripe(samples.stripe_event("invoice.paid", samples.invoice_paid("cus_DEMO01", 14700)))
show(f"{outcomes[-1]} after automatic retries: lifetime value=${hubspot.contacts[contact_id]['stripe_lifetime_value']}")

step(8, "A forged webhook arrives (wrong signing secret)")
status = send_stripe(samples.stripe_event("customer.created", cust), secret="attacker-guess")
show(f"rejected with HTTP {status}, nothing processed")

step(9, "HubSpot rejects a write permanently (e.g. a property was deleted by an admin)")
hubspot.fail_next = [PermanentError("HubSpot 400: Property 'stripe_mrr' does not exist")]
send_stripe(samples.stripe_event("customer.subscription.updated",
                                 samples.subscription("cus_DEMO01", "past_due", 4900, quantity=3)))
show(f"{outcomes[-1]}; alert sent: \"{alerter.sent[-1][0]}\"")
auth = {"Authorization": "Bearer demo-admin"}
dead = client.get("/dead-letters", headers=auth).json()
show(f"GET /dead-letters -> {[(d['id'], d['error']) for d in dead]}")

step(10, "Admin fixes the property and replays the dead letter")
replay = client.post(f"/dead-letters/{dead[0]['id']}/replay", headers=auth).json()
show(f"replay -> {replay['outcome']}; status in HubSpot: {hubspot.contacts[contact_id]['stripe_subscription_status']}")
show(f"open dead letters: {client.get('/dead-letters', headers=auth).json()}")

print("\nDone. Every step above went through the real FastAPI endpoints with real signature checks.")
