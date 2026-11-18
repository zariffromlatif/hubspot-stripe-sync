import time

import pytest

from hs_stripe_sync.signatures import (
    SignatureError,
    sign_hubspot_v3,
    sign_stripe,
    verify_hubspot_v3,
    verify_stripe,
)

SECRET = "whsec_test"
BODY = b'{"id":"evt_1"}'


def test_stripe_valid_signature_passes():
    verify_stripe(BODY, sign_stripe(BODY, SECRET), SECRET)


@pytest.mark.parametrize("header", [None, "", "garbage", "t=abc,v1=xyz"])
def test_stripe_malformed_header_rejected(header):
    with pytest.raises(SignatureError):
        verify_stripe(BODY, header, SECRET)


def test_stripe_tampered_body_rejected():
    header = sign_stripe(BODY, SECRET)
    with pytest.raises(SignatureError, match="mismatch"):
        verify_stripe(b'{"id":"evt_2"}', header, SECRET)


def test_stripe_wrong_secret_rejected():
    with pytest.raises(SignatureError):
        verify_stripe(BODY, sign_stripe(BODY, "other"), SECRET)


def test_stripe_replayed_old_event_rejected():
    old = int(time.time()) - 3600
    with pytest.raises(SignatureError, match="tolerance"):
        verify_stripe(BODY, sign_stripe(BODY, SECRET, timestamp=old), SECRET)


def test_stripe_accepts_any_of_multiple_v1_signatures():
    good = sign_stripe(BODY, SECRET)
    ts, sig = good.split(",")
    verify_stripe(BODY, f"{ts},v1=deadbeef,{sig}", SECRET)


URL = "https://sync.example.com/webhooks/hubspot"


def test_hubspot_valid_signature_passes():
    ts = str(int(time.time() * 1000))
    sig = sign_hubspot_v3("POST", URL, BODY, ts, SECRET)
    verify_hubspot_v3("POST", URL, BODY, sig, ts, SECRET)


def test_hubspot_signature_decodes_uri_like_hubspot():
    ts = str(int(time.time() * 1000))
    sig = sign_hubspot_v3("POST", URL + "?a=b:c", BODY, ts, SECRET)
    verify_hubspot_v3("POST", URL + "?a=b%3Ac", BODY, sig, ts, SECRET)


def test_hubspot_stale_timestamp_rejected():
    ts = str(int((time.time() - 600) * 1000))
    sig = sign_hubspot_v3("POST", URL, BODY, ts, SECRET)
    with pytest.raises(SignatureError, match="too old"):
        verify_hubspot_v3("POST", URL, BODY, sig, ts, SECRET)


def test_hubspot_wrong_url_rejected():
    ts = str(int(time.time() * 1000))
    sig = sign_hubspot_v3("POST", URL, BODY, ts, SECRET)
    with pytest.raises(SignatureError, match="mismatch"):
        verify_hubspot_v3("POST", "https://evil.example.com/webhooks/hubspot", BODY, sig, ts, SECRET)


def test_hubspot_missing_headers_rejected():
    with pytest.raises(SignatureError):
        verify_hubspot_v3("POST", URL, BODY, None, None, SECRET)
