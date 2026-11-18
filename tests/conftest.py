import pytest

from hs_stripe_sync.alerts import MemoryAlerter
from hs_stripe_sync.fakes import FakeHubSpot, FakeStripe
from hs_stripe_sync.store import Store
from hs_stripe_sync.sync import SyncEngine


class Env:
    def __init__(self) -> None:
        self.hubspot = FakeHubSpot()
        self.stripe = FakeStripe()
        self.store = Store(":memory:")
        self.alerter = MemoryAlerter()
        self.sleeps: list[float] = []
        self.engine = SyncEngine(
            self.hubspot, self.stripe, self.store, self.alerter,
            retry_attempts=4, sleep=self.sleeps.append,
        )


@pytest.fixture
def env() -> Env:
    return Env()
