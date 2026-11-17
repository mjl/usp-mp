"""Opt-in integration tests against the *demo* Mein Postkorb service.

Skipped unless a client certificate is configured:

    USP_MP_P12=/path/to/certificate.p12 USP_MP_P12_PASS=... pytest -m demo

Read-only on purpose: nothing is closed or deleted (the demo ignores that
anyway, per the vendor HowTo). Always uses the demo endpoint, never production.
"""

import datetime
import os

import pytest

from usp_mp.mail import Mailbox

P12 = os.environ.get("USP_MP_P12")
P12_PASS = os.environ.get("USP_MP_P12_PASS")

pytestmark = [
    pytest.mark.demo,
    pytest.mark.skipif(
        not P12 or P12_PASS is None,
        reason="set USP_MP_P12 and USP_MP_P12_PASS (may be empty) to run demo-service tests",
    ),
]


@pytest.fixture(scope="module")
def demo_mailbox():
    with Mailbox(P12, P12_PASS, prod=False) as mbox:
        yield mbox


@pytest.fixture(scope="module")
def all_deliveries(demo_mailbox):
    deliveries = list(demo_mailbox.deliveries(all=True))
    assert deliveries, "demo service returned no deliveries"
    return deliveries


def test_mailbox_targets_demo(demo_mailbox):
    assert demo_mailbox.uspmp.endpoint.startswith("https://demo-")


def test_query_accepted(all_deliveries):
    """zeep sends <Query xsi:type="..."> rather than the HowTo's <AllDeliveries>;
    getting results at all (fixture) shows the service accepts that form."""
    assert all(d.id for d in all_deliveries)


def test_new_deliveries_are_subset_of_all(demo_mailbox, all_deliveries):
    new_ids = {d.id for d in demo_mailbox.deliveries(all=False)}
    assert new_ids <= {d.id for d in all_deliveries}


def test_delivery_metadata(all_deliveries):
    for delivery in all_deliveries:
        assert isinstance(delivery.delivery_ts, datetime.datetime)
        assert isinstance(delivery.subject, str) and delivery.subject


def test_attachments_download_with_valid_checksums(all_deliveries):
    """Settles the critique's B3 *verify* items against real data."""
    seen = 0
    for delivery in all_deliveries:
        for attachment in delivery.attachments():
            algorithm, value = attachment.hash
            assert algorithm in {"SHA256", "SHA512"}
            assert isinstance(value, bytes)
            assert attachment.content(verify_checksum=True)
            seen += 1
    assert seen, "no attachments in demo data"


def test_every_delivery_has_text_mail_body(all_deliveries):
    """howto.md claims every delivery has a text/plain `mailbody` part."""
    for delivery in all_deliveries:
        assert any(a.mimetype == "text/plain" for a in delivery.attachments())
