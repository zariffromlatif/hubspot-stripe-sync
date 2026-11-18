"""In-memory HubSpot and Stripe fakes for tests and the offline demo."""
from __future__ import annotations

import copy
import itertools


class _FailureInjection:
    def __init__(self) -> None:
        self.fail_next: list[Exception] = []
        self.calls: list[tuple] = []

    def _maybe_fail(self, call: tuple) -> None:
        self.calls.append(call)
        if self.fail_next:
            raise self.fail_next.pop(0)


class FakeHubSpot(_FailureInjection):
    def __init__(self) -> None:
        super().__init__()
        self.contacts: dict[str, dict[str, str]] = {}
        self._ids = itertools.count(501)

    def find_contact_by_email(self, email: str) -> dict | None:
        self._maybe_fail(("find_contact_by_email", email))
        for cid, props in self.contacts.items():
            if props.get("email") == email:
                return {"id": cid, "properties": dict(props)}
        return None

    def get_contact(self, contact_id: str, properties: list[str]) -> dict:
        self._maybe_fail(("get_contact", contact_id))
        props = self.contacts[contact_id]
        return {"id": contact_id, "properties": {k: props.get(k) for k in properties}}

    def create_contact(self, properties: dict[str, str]) -> str:
        self._maybe_fail(("create_contact", dict(properties)))
        cid = str(next(self._ids))
        self.contacts[cid] = dict(properties)
        return cid

    def update_contact(self, contact_id: str, properties: dict[str, str]) -> None:
        self._maybe_fail(("update_contact", contact_id, dict(properties)))
        self.contacts[contact_id].update(properties)


class FakeStripe(_FailureInjection):
    def __init__(self) -> None:
        super().__init__()
        self.customers: dict[str, dict] = {}

    def add_customer(self, customer: dict) -> None:
        self.customers[customer["id"]] = copy.deepcopy(customer)

    def get_customer(self, customer_id: str) -> dict:
        self._maybe_fail(("get_customer", customer_id))
        return copy.deepcopy(self.customers[customer_id])

    def update_customer(self, customer_id: str, fields: dict[str, str], metadata: dict[str, str] | None = None) -> dict:
        self._maybe_fail(("update_customer", customer_id, dict(fields), dict(metadata or {})))
        customer = self.customers[customer_id]
        customer.update(fields)
        customer.setdefault("metadata", {}).update(metadata or {})
        return copy.deepcopy(customer)
