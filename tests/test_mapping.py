from hs_stripe_sync import samples
from hs_stripe_sync.mapping import (
    customer_to_contact_properties,
    invoice_to_contact_properties,
    join_name,
    split_name,
    subscription_mrr,
    to_major_units,
)


def test_split_and_join_name():
    assert split_name("Ada  King Lovelace") == ("Ada", "King Lovelace")
    assert split_name(None) == ("", "")
    assert join_name("Ada", "") == "Ada"
    assert join_name(" Ada ", " Lovelace ") == "Ada Lovelace"


def test_customer_mapping_normalises_email():
    props = customer_to_contact_properties(samples.customer("cus_1", " Ada@Example.COM ", "Ada Lovelace", "+44 20 1234"))
    assert props == {
        "stripe_customer_id": "cus_1", "email": "ada@example.com",
        "firstname": "Ada", "lastname": "Lovelace", "phone": "+44 20 1234",
    }


def test_mrr_normalises_intervals_and_quantity():
    assert subscription_mrr(samples.subscription("c", "active", 4900)) == 49.0
    assert subscription_mrr(samples.subscription("c", "active", 120000, interval="year")) == 100.0
    assert subscription_mrr(samples.subscription("c", "active", 1000, quantity=3)) == 30.0
    assert subscription_mrr(samples.subscription("c", "canceled", 4900)) == 0.0


def test_zero_decimal_currency():
    assert to_major_units(500, "jpy") == 500.0
    assert to_major_units(500, "usd") == 5.0


def test_invoice_adds_to_lifetime_value():
    props = invoice_to_contact_properties(samples.invoice_paid("c", 4900, paid_at=1_767_225_600), previous_ltv=100.0)
    assert props == {"stripe_lifetime_value": "149.00", "stripe_last_payment_date": "2026-01-01"}
