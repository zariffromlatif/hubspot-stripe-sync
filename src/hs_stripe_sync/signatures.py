"""Webhook signature verification for Stripe and HubSpot (v3).

Both checks use constant-time comparison and reject stale timestamps to
block replayed requests.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time


class SignatureError(Exception):
    """Raised when a webhook signature is missing, invalid or too old."""


# --- Stripe -----------------------------------------------------------------

def sign_stripe(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """Build a Stripe-Signature header value (used by tests and the demo)."""
    ts = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_stripe(
    payload: bytes,
    header: str | None,
    secret: str,
    *,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> None:
    if not header:
        raise SignatureError("missing Stripe-Signature header")
    timestamp: str | None = None
    signatures: list[str] = []
    for item in header.split(","):
        key, _, value = item.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not timestamp.isdigit() or not signatures:
        raise SignatureError("malformed Stripe-Signature header")

    expected = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in signatures):
        raise SignatureError("Stripe signature mismatch")

    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > tolerance_seconds:
        raise SignatureError("Stripe signature timestamp outside tolerance")


# --- HubSpot v3 -------------------------------------------------------------

# HubSpot signs the URI with these percent-encodings decoded.
_HUBSPOT_URI_DECODE = {
    "%3A": ":", "%2F": "/", "%3F": "?", "%40": "@", "%21": "!", "%24": "$",
    "%27": "'", "%28": "(", "%29": ")", "%2A": "*", "%2C": ",", "%3B": ";",
}


def _decode_hubspot_uri(uri: str) -> str:
    for encoded, char in _HUBSPOT_URI_DECODE.items():
        uri = uri.replace(encoded, char).replace(encoded.lower(), char)
    return uri


def sign_hubspot_v3(method: str, url: str, body: bytes, timestamp_ms: str, secret: str) -> str:
    source = method.upper() + _decode_hubspot_uri(url) + body.decode("utf-8") + timestamp_ms
    digest = hmac.new(secret.encode(), source.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_hubspot_v3(
    method: str,
    url: str,
    body: bytes,
    signature: str | None,
    timestamp_ms: str | None,
    secret: str,
    *,
    max_age_seconds: int = 300,
    now: float | None = None,
) -> None:
    """Verify X-HubSpot-Signature-v3.

    `url` must be the exact public URL HubSpot called (scheme, host, path,
    query). Behind a proxy, rebuild it from PUBLIC_BASE_URL rather than
    trusting the request's own scheme/host.
    """
    if not signature or not timestamp_ms:
        raise SignatureError("missing HubSpot signature headers")
    if not timestamp_ms.isdigit():
        raise SignatureError("malformed HubSpot timestamp")
    current_ms = (time.time() if now is None else now) * 1000
    if current_ms - int(timestamp_ms) > max_age_seconds * 1000:
        raise SignatureError("HubSpot request timestamp too old")
    expected = sign_hubspot_v3(method, url, body, timestamp_ms, secret)
    if not hmac.compare_digest(expected, signature):
        raise SignatureError("HubSpot signature mismatch")
