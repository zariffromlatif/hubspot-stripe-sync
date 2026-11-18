"""Command line: run the server, set up HubSpot properties, or reconcile drift.

    python -m hs_stripe_sync.cli serve [--port 8000]
    python -m hs_stripe_sync.cli setup-hubspot
    python -m hs_stripe_sync.cli reconcile [--fix]
"""
from __future__ import annotations

import argparse

from .config import Settings
from .hubspot import HttpHubSpotClient, HubSpotClient
from .mapping import customer_to_contact_properties
from .store import Store
from .stripe_api import HttpStripeClient, StripeClient


def _norm(value: object) -> str:
    return str(value or "").strip().lower()


def reconcile(hubspot: HubSpotClient, stripe: StripeClient, store: Store, *, fix: bool) -> list[dict]:
    """Compare every linked pair; Stripe is the source of truth for identity fields."""
    drift: list[dict] = []
    for customer_id, contact_id in store.all_links():
        expected = customer_to_contact_properties(stripe.get_customer(customer_id))
        actual = hubspot.get_contact(contact_id, list(expected)).get("properties") or {}
        diffs = {k: {"hubspot": actual.get(k), "stripe": v} for k, v in expected.items() if _norm(actual.get(k)) != _norm(v)}
        if diffs:
            drift.append({"stripe_customer_id": customer_id, "hubspot_contact_id": contact_id, "diffs": diffs})
            if fix:
                hubspot.update_contact(contact_id, {k: d["stripe"] for k, d in diffs.items()})
    return drift


def main() -> None:
    parser = argparse.ArgumentParser(prog="hs_stripe_sync")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("setup-hubspot")
    rec = sub.add_parser("reconcile")
    rec.add_argument("--fix", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    if args.command == "serve":
        import uvicorn

        from .app import create_app

        uvicorn.run(create_app(settings), host=args.host, port=args.port)
    elif args.command == "setup-hubspot":
        created = HttpHubSpotClient(settings.hubspot_token).ensure_custom_properties()
        print("created:", ", ".join(created) or "nothing (already set up)")
    elif args.command == "reconcile":
        drift = reconcile(
            HttpHubSpotClient(settings.hubspot_token),
            HttpStripeClient(settings.stripe_api_key),
            Store(settings.db_path),
            fix=args.fix,
        )
        for item in drift:
            print(item)
        print(f"{len(drift)} record(s) drifted" + (" (fixed)" if args.fix and drift else ""))


if __name__ == "__main__":
    main()
