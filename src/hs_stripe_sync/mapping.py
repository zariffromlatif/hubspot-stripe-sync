"""Pure field mapping between Stripe objects and HubSpot contact properties."""
from __future__ import annotations

from datetime import datetime, timezone

# HubSpot property -> Stripe customer field, for the HubSpot -> Stripe direction.
HUBSPOT_TO_STRIPE_FIELDS = {"email": "email", "phone": "phone"}
HUBSPOT_NAME_FIELDS = {"firstname", "lastname"}
SYNCED_HUBSPOT_PROPERTIES = set(HUBSPOT_TO_STRIPE_FIELDS) | HUBSPOT_NAME_FIELDS

# Months per billing interval, used to normalise prices to monthly revenue.
_MONTHS_PER_INTERVAL = {"day": 12 / 365, "week": 12 / 52, "month": 1.0, "year": 12.0}

# Stripe currencies without minor units (amounts are already whole units).
_ZERO_DECIMAL = {"bif", "clp", "djf", "gnf", "jpy", "kmf", "krw", "mga", "pyg", "rwf", "ugx", "vnd", "vuv", "xaf", "xof", "xpf"}


def to_major_units(amount: int, currency: str) -> float:
    return float(amount) if currency.lower() in _ZERO_DECIMAL else amount / 100


def split_name(full_name: str | None) -> tuple[str, str]:
    parts = (full_name or "").strip().split()
    if not parts:
        return "", ""
    return parts[0], " ".join(parts[1:])


def join_name(first: str | None, last: str | None) -> str:
    return " ".join(p for p in [(first or "").strip(), (last or "").strip()] if p)


def customer_to_contact_properties(customer: dict) -> dict[str, str]:
    props: dict[str, str] = {"stripe_customer_id": customer["id"]}
    if customer.get("email"):
        props["email"] = customer["email"].strip().lower()
    first, last = split_name(customer.get("name"))
    if first:
        props["firstname"] = first
    if last:
        props["lastname"] = last
    if customer.get("phone"):
        props["phone"] = customer["phone"]
    return props


def subscription_mrr(subscription: dict) -> float:
    """Monthly recurring revenue of a subscription, in major currency units."""
    if subscription.get("status") in {"canceled", "incomplete_expired"}:
        return 0.0
    total = 0.0
    for item in subscription.get("items", {}).get("data", []):
        price = item.get("price") or {}
        recurring = price.get("recurring") or {}
        interval = recurring.get("interval")
        count = recurring.get("interval_count") or 1
        if interval not in _MONTHS_PER_INTERVAL or price.get("unit_amount") is None:
            continue
        amount = to_major_units(price["unit_amount"], price.get("currency", "usd")) * (item.get("quantity") or 1)
        total += amount / (_MONTHS_PER_INTERVAL[interval] * count)
    return round(total, 2)


def subscription_to_contact_properties(subscription: dict) -> dict[str, str]:
    return {
        "stripe_subscription_status": subscription.get("status", "unknown"),
        "stripe_mrr": f"{subscription_mrr(subscription):.2f}",
    }


def invoice_to_contact_properties(invoice: dict, previous_ltv: float) -> dict[str, str]:
    paid = to_major_units(invoice.get("amount_paid", 0), invoice.get("currency", "usd"))
    paid_at = (invoice.get("status_transitions") or {}).get("paid_at") or invoice.get("created")
    props = {"stripe_lifetime_value": f"{previous_ltv + paid:.2f}"}
    if paid_at:
        props["stripe_last_payment_date"] = datetime.fromtimestamp(paid_at, tz=timezone.utc).strftime("%Y-%m-%d")
    return props
