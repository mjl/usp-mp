"""Behaviour of Mailbox / Delivery / Attachment against the fake service.

Tests marked ``xfail(strict=True)`` document open findings from docs/critique.md.
When a fix lands they start passing, which strict mode reports as a failure:
remove the marker then.
"""

import datetime
import hashlib

import pytest
import requests

from fakes import FakeAttachment
from usp_mp.adapter import USP_MP_Adapter


def ids(deliveries):
    return [d.id for d in deliveries]


# --- Mailbox construction ---------------------------------------------------------

def test_mailbox_passes_credentials_and_endpoint(fake_adapters):
    from usp_mp.mail import Mailbox

    Mailbox("cert.p12", "pw", prod=True)

    [adapter] = fake_adapters
    assert (adapter.p12_path, adapter.p12_pass, adapter.prod) == ("cert.p12", "pw", True)


def test_mailbox_defaults_to_demo(fake_adapters, mailbox):
    assert fake_adapters[0].prod is False


# --- querying -----------------------------------------------------------------------

def test_empty_mailbox_yields_nothing(mailbox, service):
    assert list(mailbox.deliveries(all=True)) == []
    assert list(mailbox.deliveries(all=False)) == []


def test_all_deliveries_in_server_order(mailbox, service):
    expected = [d.delivery_id for d in service.add_deliveries(3)]
    service.deliveries[expected[1]].read = True

    assert ids(mailbox.deliveries(all=True)) == expected


def test_new_only_skips_read_deliveries(mailbox, service):
    unread, read, unread2 = service.add_deliveries(3)
    read.read = True

    assert ids(mailbox.deliveries(all=False)) == [unread.delivery_id, unread2.delivery_id]


def test_default_is_all_deliveries(mailbox, service):
    service.add_delivery(read=True)

    assert len(list(mailbox.deliveries())) == 1


def test_query_selectors_and_version(mailbox, service):
    list(mailbox.deliveries(all=True))
    list(mailbox.deliveries(all=False))

    (_, all_sel, v1), (_, new_sel, v2) = service.calls_to("QueryDeliveries")
    assert all_sel._xsd_type.name == "AllDeliveriesType"
    assert (all_sel.Paging.Start, all_sel.Paging.Limit) == (0, 100)
    assert new_sel._xsd_type.name == "NewDeliveriesOnlyType"
    assert new_sel.Limit == 100
    assert v1 == v2 == USP_MP_Adapter.VERSION


def test_query_error_raises(mailbox, service):
    service.fail_next("QueryDeliveries", "123", "Mailbox locked")

    with pytest.raises(ValueError, match="query deliveries.*123.*Mailbox locked"):
        list(mailbox.deliveries())


def test_connection_closed_after_full_iteration(mailbox, service):
    service.add_deliveries(2)

    for _ in mailbox.deliveries():
        assert service.open_connections == 1
    assert service.open_connections == 0


@pytest.mark.xfail(strict=True, reason="critique B2: deliveries() stops after the first 100")
def test_all_deliveries_beyond_first_page(mailbox, service):
    service.add_deliveries(250)

    assert len(list(mailbox.deliveries(all=True))) == 250


# --- Delivery -----------------------------------------------------------------------

def test_delivery_metadata(mailbox, service):
    fake = service.add_delivery("Bescheid 2026")

    [delivery] = mailbox.deliveries()

    assert delivery.id == fake.delivery_id
    assert delivery.subject == "Bescheid 2026"
    assert delivery.delivery_ts == fake.timestamp
    assert isinstance(delivery.delivery_ts, datetime.datetime)
    assert repr(delivery) == f"Delivery<{fake.delivery_id} [Bescheid 2026]>"


def test_get_delivery_error_raises(mailbox, service):
    service.add_delivery()
    service.fail_next("GetDelivery", "000", "Unkown uuid.")

    with pytest.raises(ValueError, match="get delivery.*000"):
        list(mailbox.deliveries())


def test_close_marks_read(mailbox, service):
    fake = service.add_delivery()

    for delivery in mailbox.deliveries(all=False):
        delivery.close()

    assert fake.read
    assert list(mailbox.deliveries(all=False)) == []
    assert service.calls_to("CloseDelivery") == [("CloseDelivery", fake.delivery_id, USP_MP_Adapter.VERSION)]


def test_close_with_delete(mailbox, service):
    fake = service.add_delivery()

    for delivery in mailbox.deliveries():
        delivery.close(delete=True)

    assert fake.delivery_id not in service.deliveries
    assert [c[0] for c in service.calls if c[0].endswith("Delivery")] == [
        "GetDelivery", "CloseDelivery", "DeleteDelivery"
    ]


@pytest.mark.parametrize("operation", ["CloseDelivery", "DeleteDelivery"])
def test_close_and_delete_errors_raise(mailbox, service, operation):
    service.add_delivery()
    [delivery] = mailbox.deliveries()
    service.fail_next(operation)

    with pytest.raises(ValueError, match="999"):
        delivery.close() if operation == "CloseDelivery" else delivery.delete()


# --- attachments --------------------------------------------------------------------

def test_attachment_properties(mailbox, service):
    att = FakeAttachment(b"%PDF-1.7 ...", "Bescheid.pdf", "application/pdf")
    service.add_delivery(attachments=[att])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.id == att.attachment_id
    assert attachment.filename == "Bescheid.pdf"
    assert attachment.mimetype == "application/pdf"
    assert attachment.hash == ("SHA256", hashlib.sha256(att.content).digest())
    assert repr(attachment) == f"Attachment<{att.attachment_id} [Bescheid.pdf]>"


def test_default_delivery_has_mail_body(mailbox, service):
    service.add_delivery()

    [delivery] = mailbox.deliveries()
    [body] = delivery.attachments()

    assert (body.filename, body.mimetype) == ("mailbody", "text/plain")


def test_delivery_without_attachments(mailbox, service):
    service.add_delivery(attachments=[])

    [delivery] = mailbox.deliveries()

    assert list(delivery.attachments()) == []


@pytest.mark.parametrize("algorithm", ["SHA256", "SHA512"])
def test_content_verified(mailbox, service, algorithm):
    att = FakeAttachment(b"payload", algorithm=algorithm)
    service.add_delivery(attachments=[att])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.content(verify_checksum=True) == b"payload"
    assert service.calls_to("download_attachment") == [
        ("download_attachment", delivery.id, att.attachment_id)
    ]


def test_checksum_mismatch_raises(mailbox, service):
    service.add_delivery(attachments=[FakeAttachment(b"payload", checksum=b"\0" * 32)])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    with pytest.raises(ValueError, match="do not match checksum"):
        attachment.content()


def test_checksum_check_can_be_skipped(mailbox, service):
    service.add_delivery(attachments=[FakeAttachment(b"payload", checksum=b"\0" * 32)])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.content(verify_checksum=False) == b"payload"


def test_download_error_propagates(mailbox, service):
    fake = service.add_delivery()

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()
    fake.attachments.clear()  # server no longer has it -> HTTP 400

    with pytest.raises(requests.HTTPError):
        attachment.content()


# --- filename sanitising ------------------------------------------------------------

@pytest.mark.parametrize(
    ("filename", "mimetype", "expected"),
    [
        ("Bescheid.pdf", "application/pdf", "bescheid.pdf"),
        ("Rechnung März 2026.pdf", "application/pdf", "rechnung_m_rz_2026.pdf"),
        ("mailbody", "text/plain", "mailbody.txt"),
        ("data.bin", "application/x-unknown-type", "data.bin"),
    ],
)
def test_sanitized_filename(mailbox, service, filename, mimetype, expected):
    service.add_delivery(attachments=[FakeAttachment(b"x", filename, mimetype)])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.sanitized_filename == expected


@pytest.mark.xfail(strict=True, reason="critique B9: runs of underscores are collapsed only once")
def test_sanitized_filename_collapses_all_underscores(mailbox, service):
    service.add_delivery(attachments=[FakeAttachment(b"x", "a   b.pdf", "application/pdf")])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.sanitized_filename == "a_b.pdf"
