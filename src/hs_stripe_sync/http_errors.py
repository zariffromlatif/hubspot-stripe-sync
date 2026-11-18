"""Translate HTTP responses into RetryableError / PermanentError."""
from __future__ import annotations

import httpx

from .retry import PermanentError, RetryableError


def raise_for_status(service: str, response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    detail = response.text[:500]
    if status == 429 or status >= 500:
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after is not None else None
        except ValueError:
            delay = None
        raise RetryableError(f"{service} {status}: {detail}", retry_after=delay)
    raise PermanentError(f"{service} {status}: {detail}")


def request(client: httpx.Client, service: str, method: str, url: str, **kwargs) -> httpx.Response:
    try:
        response = client.request(method, url, **kwargs)
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise RetryableError(f"{service} network error: {exc}") from exc
    raise_for_status(service, response)
    return response
