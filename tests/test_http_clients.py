"""The real HTTP clients, exercised against httpx.MockTransport (no network)."""
import json

import httpx
import pytest

from hs_stripe_sync.hubspot import HttpHubSpotClient
from hs_stripe_sync.retry import PermanentError, RetryableError
from hs_stripe_sync.stripe_api import HttpStripeClient


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_hubspot_search_by_email_request_shape():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"results": [{"id": "77", "properties": {}}]})

    hs = HttpHubSpotClient("pat-123", http=_client(handler))
    assert hs.find_contact_by_email("a@b.co")["id"] == "77"
    assert seen["url"] == "https://api.hubapi.com/crm/v3/objects/contacts/search"
    assert seen["auth"] == "Bearer pat-123"
    assert seen["body"]["filterGroups"][0]["filters"][0] == {"propertyName": "email", "operator": "EQ", "value": "a@b.co"}


def test_hubspot_429_maps_to_retryable_with_retry_after():
    hs = HttpHubSpotClient("t", http=_client(lambda r: httpx.Response(429, headers={"Retry-After": "3"}, text="slow down")))
    with pytest.raises(RetryableError) as exc:
        hs.update_contact("1", {"phone": "1"})
    assert exc.value.retry_after == 3.0


def test_hubspot_400_maps_to_permanent():
    hs = HttpHubSpotClient("t", http=_client(lambda r: httpx.Response(400, text="bad property")))
    with pytest.raises(PermanentError):
        hs.create_contact({"x": "y"})


def test_hubspot_ensure_properties_tolerates_existing():
    def handler(request: httpx.Request):
        return httpx.Response(409, text="already exists")

    assert HttpHubSpotClient("t", http=_client(handler)).ensure_custom_properties() == []


def test_stripe_update_is_form_encoded_with_metadata():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"id": "cus_1"})

    HttpStripeClient("sk_test", http=_client(handler)).update_customer(
        "cus_1", {"phone": "+1 555"}, metadata={"hubspot_contact_id": "77"}
    )
    assert seen["url"] == "https://api.stripe.com/v1/customers/cus_1"
    assert "phone=%2B1+555" in seen["body"]
    assert "metadata%5Bhubspot_contact_id%5D=77" in seen["body"]


def test_network_errors_are_retryable():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(RetryableError):
        HttpStripeClient("sk", http=_client(handler)).get_customer("cus_1")
