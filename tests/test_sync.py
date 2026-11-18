from hs_stripe_sync import samples
from hs_stripe_sync.retry import PermanentError, RetryableError


def _create(env, cid="cus_1", email="ada@example.com", name="Ada Lovelace"):
    cust = samples.customer(cid, email, name)
    env.stripe.add_customer(cust)
    return env.engine.handle_stripe_event(samples.stripe_event("customer.created", cust))


def test_customer_created_creates_contact_and_backlinks(env):
    assert _create(env) == "contact_upserted"
    (cid, props), = env.hubspot.contacts.items()
    assert props["email"] == "ada@example.com" and props["stripe_customer_id"] == "cus_1"
    assert env.stripe.customers["cus_1"]["metadata"]["hubspot_contact_id"] == cid


def test_existing_contact_is_matched_by_email_not_duplicated(env):
    existing = env.hubspot.create_contact({"email": "ada@example.com", "lifecyclestage": "lead"})
    _create(env)
    assert list(env.hubspot.contacts) == [existing]
    assert env.hubspot.contacts[existing]["lifecyclestage"] == "lead"  # untouched fields kept


def test_duplicate_delivery_is_skipped(env):
    cust = samples.customer("cus_1", "ada@example.com", "Ada Lovelace")
    env.stripe.add_customer(cust)
    event = samples.stripe_event("customer.created", cust)
    assert env.engine.handle_stripe_event(event) == "contact_upserted"
    calls = len(env.hubspot.calls)
    assert env.engine.handle_stripe_event(event) == "duplicate"
    assert len(env.hubspot.calls) == calls


def test_subscription_before_customer_event_still_syncs(env):
    env.stripe.add_customer(samples.customer("cus_9", "late@example.com", "Late Arrival"))
    outcome = env.engine.handle_stripe_event(
        samples.stripe_event("customer.subscription.created", samples.subscription("cus_9", "active", 9900))
    )
    assert outcome == "subscription_synced"
    (props,) = env.hubspot.contacts.values()
    assert props["email"] == "late@example.com"
    assert props["stripe_mrr"] == "99.00" and props["stripe_subscription_status"] == "active"


def test_invoice_paid_accumulates_lifetime_value(env):
    _create(env)
    for _ in range(2):
        env.engine.handle_stripe_event(samples.stripe_event("invoice.paid", samples.invoice_paid("cus_1", 4900)))
    (props,) = env.hubspot.contacts.values()
    assert props["stripe_lifetime_value"] == "98.00"


def test_hubspot_phone_change_updates_stripe(env):
    _create(env)
    contact_id = next(iter(env.hubspot.contacts))
    outcome = env.engine.handle_hubspot_event(samples.hubspot_property_change(contact_id, "phone", "+1 555 0100"))
    assert outcome == "stripe_updated"
    assert env.stripe.customers["cus_1"]["phone"] == "+1 555 0100"


def test_hubspot_name_change_composes_full_name(env):
    _create(env)
    contact_id = next(iter(env.hubspot.contacts))
    env.hubspot.contacts[contact_id]["lastname"] = "Byron"
    env.engine.handle_hubspot_event(samples.hubspot_property_change(contact_id, "lastname", "Byron"))
    assert env.stripe.customers["cus_1"]["name"] == "Ada Byron"


def test_loop_prevention_both_directions(env):
    _create(env)
    contact_id = next(iter(env.hubspot.contacts))
    # HubSpot fires propertyChange for the email WE just wrote -> must not bounce back to Stripe.
    echo = samples.hubspot_property_change(contact_id, "email", "ada@example.com")
    assert env.engine.handle_hubspot_event(echo) == "echo_suppressed"

    # A user edits phone in HubSpot -> we update Stripe -> Stripe fires customer.updated -> suppressed.
    env.engine.handle_hubspot_event(samples.hubspot_property_change(contact_id, "phone", "+1 555 0100"))
    cust = env.stripe.customers["cus_1"]
    stripe_echo = samples.stripe_event("customer.updated", cust, previous_attributes={"phone": None})
    assert env.engine.handle_stripe_event(stripe_echo) == "echo_suppressed"


def test_metadata_only_update_is_ignored(env):
    _create(env)
    cust = env.stripe.customers["cus_1"]
    event = samples.stripe_event("customer.updated", cust, previous_attributes={"metadata": {}})
    assert env.engine.handle_stripe_event(event) == "no_synced_field_changed"


def test_unlinked_hubspot_contact_is_ignored(env):
    cid = env.hubspot.create_contact({"email": "prospect@example.com"})
    assert env.engine.handle_hubspot_event(samples.hubspot_property_change(cid, "phone", "1")) == "not_linked"


def test_irrelevant_hubspot_events_ignored(env):
    event = samples.hubspot_property_change("1", "hs_lead_status", "OPEN")
    assert env.engine.handle_hubspot_event(event) == "ignored"


def test_rate_limits_are_retried_then_succeed(env):
    env.hubspot.fail_next = [RetryableError("429", retry_after=2.0), RetryableError("503")]
    assert _create(env) == "contact_upserted"
    assert env.sleeps[0] == 2.0  # honoured Retry-After
    assert len(env.sleeps) == 2
    assert not env.alerter.sent


def test_permanent_failure_dead_letters_alerts_and_replays(env):
    env.hubspot.fail_next = [PermanentError("HubSpot 400: Property values were not valid")]
    assert _create(env) == "dead_lettered"
    (dead,) = env.store.open_dead_letters()
    assert "Property values were not valid" in dead.error
    assert env.alerter.sent and "dead letter" in env.alerter.sent[0][0]

    # Fix deployed; replay succeeds and the dead letter is closed.
    assert env.engine.replay_dead_letter(dead.id) == "contact_upserted"
    assert env.store.open_dead_letters() == []
    assert len(env.hubspot.contacts) == 1


def test_retries_exhausted_dead_letters(env):
    env.hubspot.fail_next = [RetryableError("503")] * 10
    assert _create(env) == "dead_lettered"
    assert len(env.sleeps) == 3  # 4 attempts -> 3 waits
