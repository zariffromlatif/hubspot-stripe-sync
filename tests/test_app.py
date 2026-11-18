import json
import time

import pytest
from fastapi.testclient import TestClient

from hs_stripe_sync import samples
from hs_stripe_sync.app import create_app
from hs_stripe_sync.config import Settings
from hs_stripe_sync.retry import PermanentError
from hs_stripe_sync.signatures import sign_hubspot_v3, sign_stripe

SETTINGS = Settings(
    hubspot_token="x", hubspot_client_secret="hs_secret", stripe_api_key="sk_test_x",
    stripe_webhook_secret="whsec_x", public_base_url="https://sync.example.com", admin_token="admin123",
)


@pytest.fixture
def client(env):
    return TestClient(create_app(SETTINGS, engine=env.engine))


def _post_stripe(client, event, secret="whsec_x"):
    body = json.dumps(event).encode()
    return client.post("/webhooks/stripe", content=body, headers={"Stripe-Signature": sign_stripe(body, secret)})


def test_signed_stripe_webhook_is_processed(client, env):
    cust = samples.customer("cus_1", "ada@example.com", "Ada Lovelace")
    env.stripe.add_customer(cust)
    response = _post_stripe(client, samples.stripe_event("customer.created", cust))
    assert response.status_code == 200
    assert len(env.hubspot.contacts) == 1


def test_bad_stripe_signature_rejected(client, env):
    response = _post_stripe(client, samples.stripe_event("customer.created", {}), secret="wrong")
    assert response.status_code == 400
    assert env.hubspot.calls == []


def test_hubspot_webhook_signature_checked_against_public_url(client, env):
    body = json.dumps([samples.hubspot_property_change("1", "hs_lead_status", "OPEN")]).encode()
    ts = str(int(time.time() * 1000))
    good = sign_hubspot_v3("POST", "https://sync.example.com/webhooks/hubspot", body, ts, "hs_secret")
    ok = client.post("/webhooks/hubspot", content=body,
                     headers={"X-HubSpot-Signature-v3": good, "X-HubSpot-Request-Timestamp": ts})
    assert ok.status_code == 200
    bad = client.post("/webhooks/hubspot", content=body,
                      headers={"X-HubSpot-Signature-v3": "nope", "X-HubSpot-Request-Timestamp": ts})
    assert bad.status_code == 401


def test_dead_letter_endpoints_require_admin_and_replay(client, env):
    cust = samples.customer("cus_1", "ada@example.com", "Ada Lovelace")
    env.stripe.add_customer(cust)
    env.hubspot.fail_next = [PermanentError("HubSpot 400")]
    _post_stripe(client, samples.stripe_event("customer.created", cust))

    assert client.get("/dead-letters").status_code == 401
    auth = {"Authorization": "Bearer admin123"}
    (dead,) = client.get("/dead-letters", headers=auth).json()
    replay = client.post(f"/dead-letters/{dead['id']}/replay", headers=auth).json()
    assert replay["outcome"] == "contact_upserted"
    assert client.get("/dead-letters", headers=auth).json() == []
