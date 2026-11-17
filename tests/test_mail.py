"""Behaviour of Mailbox / Delivery / Attachment against the fake service.

Tests marked ``xfail(strict=True)`` document open findings from docs/critique.md.
When a fix lands they start passing, which strict mode reports as a failure:
remove the marker then.
"""

import datetime
import hashlib

import pytest
import requests

from usp_mp.errors import ApiError, ChecksumError, MailboxStateError
from usp_mp.mail import Contact

from fakes import FakeAttachment, physical_person
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


# --- lifecycle: Mailbox is a context manager --------------------------------------

def make_mailbox():
    from usp_mp.mail import Mailbox

    return Mailbox("fake.p12", "secret")


def test_with_block_opens_and_closes_connection(fake_adapters, service):
    with make_mailbox() as mbox:
        assert service.open_connections == 1
        list(mbox.deliveries())
    assert service.open_connections == 0


def test_one_connection_per_mailbox(fake_adapters, service):
    service.add_deliveries(2)

    with make_mailbox() as mbox:
        list(mbox.deliveries(all=True))
        list(mbox.deliveries(all=False))
        list(mbox.deliveries())

    assert fake_adapters[0].connections_opened == 1


def test_construction_does_not_connect(fake_adapters, service):
    make_mailbox()

    assert fake_adapters[0].connections_opened == 0


def test_deliveries_without_with_fails_immediately(fake_adapters):
    mbox = make_mailbox()

    with pytest.raises(MailboxStateError, match="not open.*with Mailbox"):
        mbox.deliveries()  # raises on the call, not on first iteration


def test_deliveries_after_close_fails(fake_adapters):
    with make_mailbox() as mbox:
        pass

    with pytest.raises(MailboxStateError, match="Mailbox is closed"):
        mbox.deliveries()
    with pytest.raises(MailboxStateError, match="Mailbox is closed"):
        mbox.service


def test_mailbox_cannot_be_reopened(fake_adapters):
    mbox = make_mailbox()
    with mbox:
        with pytest.raises(MailboxStateError, match="only be opened once"):
            mbox.__enter__()
    with pytest.raises(MailboxStateError, match="only be opened once"):
        with mbox:
            pass


def test_close_is_idempotent_and_closes(fake_adapters, service):
    mbox = make_mailbox().__enter__()

    mbox.close()
    mbox.close()

    assert service.open_connections == 0
    with pytest.raises(MailboxStateError, match="Mailbox is closed"):
        mbox.deliveries()


def test_closed_before_open_cannot_be_opened(fake_adapters):
    mbox = make_mailbox()
    mbox.close()

    with pytest.raises(MailboxStateError):
        with mbox:
            pass


def test_connection_closed_when_block_raises(fake_adapters, service):
    with pytest.raises(KeyError):
        with make_mailbox():
            raise KeyError("boom")

    assert service.open_connections == 0


def test_objects_from_closed_mailbox(fake_adapters, service):
    service.add_delivery("Bescheid")

    with make_mailbox() as mbox:
        [delivery] = mbox.deliveries()
        [attachment] = delivery.attachments()

    # already-fetched data stays readable ...
    assert delivery.subject == "Bescheid"
    assert attachment.filename == "mailbody"
    # ... but nothing can reach the service any more
    for action in (attachment.content, delivery.close, delivery.delete):
        with pytest.raises(MailboxStateError, match="closed"):
            action()


def test_raw_soap_access(fake_adapters, service):
    fake = service.add_delivery()

    with make_mailbox() as mbox:
        r = mbox.service.GetDelivery(fake.delivery_id, Version=mbox.uspmp.VERSION)

    assert r.Delivery.DeliveryID == fake.delivery_id


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
    assert (all_sel.Paging.Start, all_sel.Paging.Limit) == (0, 1000)
    assert new_sel._xsd_type.name == "NewDeliveriesOnlyType"
    assert new_sel.Limit == 1000
    assert v1 == v2 == USP_MP_Adapter.VERSION


def test_query_error_raises(mailbox, service):
    service.fail_next("QueryDeliveries", "123", "Mailbox locked")

    with pytest.raises(ApiError, match=r"QueryDeliveries failed: \[123\] Mailbox locked") as excinfo:
        list(mailbox.deliveries())

    assert (excinfo.value.code, excinfo.value.message, excinfo.value.operation) == (
        "123", "Mailbox locked", "QueryDeliveries"
    )




# --- pagination ---------------------------------------------------------------------

def paging_calls(service):
    return [(sel.Paging.Start, sel.Paging.Limit) for _, sel, _ in service.calls_to("QueryDeliveries")]


def test_all_deliveries_beyond_first_page(mailbox, service):
    """Critique B2: previously stopped after 100."""
    service.add_deliveries(250)

    assert len(list(mailbox.deliveries(all=True))) == 250


def test_pages_through_large_mailbox(mailbox, service):
    expected = [d.delivery_id for d in service.add_deliveries(2500)]

    assert ids(mailbox.deliveries(all=True)) == expected
    assert paging_calls(service) == [(0, 1000), (1000, 1000), (2000, 1000)]


def test_exact_page_multiple_needs_no_extra_query(mailbox, service):
    service.add_deliveries(2000)

    assert len(list(mailbox.deliveries(all=True))) == 2000
    assert paging_calls(service) == [(0, 1000), (1000, 1000)]


def test_ids_are_queried_on_call_deliveries_fetched_lazily(mailbox, service):
    service.add_deliveries(3)

    iterator = mailbox.deliveries()

    assert len(service.calls_to("QueryDeliveries")) == 1
    assert service.calls_to("GetDelivery") == []
    next(iterator)
    assert len(service.calls_to("GetDelivery")) == 1


@pytest.mark.parametrize(("limit", "expected_calls"), [
    (5, [(0, 5)]),
    (1000, [(0, 1000)]),
    (1500, [(0, 1000), (1000, 500)]),
])
def test_limit_all(mailbox, service, limit, expected_calls):
    expected = [d.delivery_id for d in service.add_deliveries(2500)][:limit]

    assert ids(mailbox.deliveries(all=True, limit=limit)) == expected
    assert paging_calls(service) == expected_calls


def test_limit_larger_than_mailbox(mailbox, service):
    service.add_deliveries(3)

    assert len(list(mailbox.deliveries(limit=50))) == 3


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit(mailbox, limit):
    with pytest.raises(ValueError, match="limit must be at least 1"):
        mailbox.deliveries(limit=limit)


def test_deleting_while_iterating_skips_nothing(mailbox, service):
    """IDs are collected before yielding, so deletions can't shift the server-side offsets."""
    expected = [d.delivery_id for d in service.add_deliveries(1500)]

    seen = []
    for delivery in mailbox.deliveries(all=True):
        seen.append(delivery.id)
        delivery.close(delete=True)

    assert seen == expected
    assert service.deliveries == {}


def test_delivery_arriving_mid_paging_is_not_duplicated(mailbox, service, monkeypatch):
    service.add_deliveries(1500)
    original = service.QueryDeliveries

    def query_then_new_mail(selector, Version=None):
        response = original(selector, Version=Version)
        if len(service.calls_to("QueryDeliveries")) == 1:
            # new delivery lands at the front: page 2 now starts one ID earlier
            new = service.add_delivery("late arrival")
            service.deliveries = {new.delivery_id: new, **{k: v for k, v in service.deliveries.items() if k != new.delivery_id}}
        return response

    monkeypatch.setattr(service, "QueryDeliveries", query_then_new_mail)

    result = ids(mailbox.deliveries(all=True))

    assert len(result) == len(set(result))
    assert len(result) == 1500  # the late arrival comes with the next query


def test_inconsistent_hitcount_stops_on_empty_page(mailbox, service, monkeypatch):
    service.add_deliveries(3)
    original = service.QueryDeliveries

    def inflated(selector, Version=None):
        response = original(selector, Version=Version)
        response.ResultsList.HitCount = 10_000
        return response

    monkeypatch.setattr(service, "QueryDeliveries", inflated)

    assert len(list(mailbox.deliveries(all=True))) == 3
    assert paging_calls(service) == [(0, 1000), (3, 1000)]


def test_new_only_is_capped_and_warns(mailbox, service, caplog):
    service.add_deliveries(1200)

    with caplog.at_level("WARNING", logger="usp_mp.mail"):
        result = list(mailbox.deliveries(all=False))

    assert len(result) == 1000
    assert "1200 new deliveries, but only 1000 returned" in caplog.text


def test_new_only_with_limit_does_not_warn(mailbox, service, caplog):
    service.add_deliveries(20)

    with caplog.at_level("WARNING", logger="usp_mp.mail"):
        result = list(mailbox.deliveries(all=False, limit=5))

    assert len(result) == 5
    assert service.calls_to("QueryDeliveries")[0][1].Limit == 5
    assert caplog.text == ""


def test_new_only_without_truncation_does_not_warn(mailbox, service, caplog):
    service.add_deliveries(20)

    with caplog.at_level("WARNING", logger="usp_mp.mail"):
        assert len(list(mailbox.deliveries(all=False))) == 20
    assert caplog.text == ""


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

    with pytest.raises(ApiError, match=r"GetDelivery failed: \[000\]"):
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

    with pytest.raises(ApiError, match=rf"{operation} failed: \[999\]"):
        delivery.close() if operation == "CloseDelivery" else delivery.delete()


def test_normal_delivery_logs_no_schema_warning(mailbox, service, caplog):
    service.add_delivery()

    with caplog.at_level("WARNING", logger="usp_mp.mail"):
        [delivery] = mailbox.deliveries()

    assert delivery.delivery.Delivery.Sender.CorporateBody.FullName == "Bundeskanzleramt"
    assert caplog.text == ""


def test_unknown_response_elements_are_logged(mailbox, service, monkeypatch, caplog):
    """Schema drift on the service side: retrieval keeps working, but it is visible."""
    from test_wsdl import RECORDED_GET_DELIVERY, parse_get_delivery

    fake = service.add_delivery()
    xml = RECORDED_GET_DELIVERY.read_bytes().replace(
        b"</p:CorporateBody>", b"</p:CorporateBody><p:FutureElement>x</p:FutureElement>", 1
    )
    monkeypatch.setattr(service, "GetDelivery", lambda delivery_id, Version=None: parse_get_delivery(xml, strict=False))

    with caplog.at_level("WARNING", logger="usp_mp.mail"):
        [delivery] = mailbox.deliveries()

    assert delivery.subject == "Test-Nachricht: 01"
    assert f"GetDelivery response for {fake.delivery_id} contains elements" in caplog.text
    assert "FutureElement" in caplog.text


# --- typed accessors: sender, receiver, metadata -------------------------------------

def test_sender_and_receiver_corporate_bodies(mailbox, service):
    service.add_delivery()

    [delivery] = mailbox.deliveries()

    assert delivery.sender == Contact(
        kind="corporate_body", name="Bundeskanzleramt",
        identification_type="urn:publicid:gv.at:baseid+XGLN", identification_value="9110002628957",
    )
    assert delivery.receiver.kind == "corporate_body"
    assert delivery.receiver.name == "Testfirma GmbH"
    assert delivery.receiver.identification_value == "123456a"


def test_physical_person_contact(mailbox, service):
    service.add_delivery(sender=physical_person("Erika", "Musterfrau", datetime.date(1980, 1, 2)))

    [delivery] = mailbox.deliveries()

    assert delivery.sender == Contact(
        kind="physical_person", name="Erika Musterfrau", given_name="Erika", family_name="Musterfrau",
        date_of_birth=datetime.date(1980, 1, 2),
    )


def test_contact_without_person_or_identification(mailbox, service):
    service.add_delivery(sender={})

    [delivery] = mailbox.deliveries()

    assert delivery.sender == Contact(kind=None, name=None)


def test_metadata_accessors(mailbox, service):
    fake = service.add_delivery(gz="GZ 2026-0815", quality="RSb")

    [delivery] = mailbox.deliveries()

    assert delivery.app_delivery_id == f"app-{fake.delivery_id[:8]}"
    assert delivery.zs_delivery_id == f"zs-{fake.delivery_id[:8]}"
    assert delivery.delivery_service == "Meinpostkorb"
    assert delivery.origin == "AT:X:999-MPK"
    assert delivery.gz == "GZ 2026-0815"
    assert delivery.delivery_quality == "RSb"
    assert delivery.raw is delivery.delivery.Delivery


def test_optional_metadata_missing(mailbox, service):
    service.add_delivery()

    [delivery] = mailbox.deliveries()

    assert delivery.gz is None


def test_attachment_size_and_raw(mailbox, service):
    att = FakeAttachment(b"12345", "a.pdf")
    service.add_delivery(attachments=[att])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.size == 5
    assert attachment.document_class is None
    assert attachment.raw is attachment.attachment


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

    with pytest.raises(ChecksumError, match="do not match checksum"):
        attachment.content()


@pytest.mark.parametrize("algorithm", ["MD5", "sha256", "SHA-256"])
def test_unsupported_checksum_algorithm_raises(mailbox, service, algorithm):
    """Only the schema's SHA256/SHA512 are accepted, spelled exactly as in the XSD."""
    service.add_delivery(attachments=[FakeAttachment(b"payload", algorithm=algorithm, checksum=b"\0" * 32)])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    with pytest.raises(ChecksumError, match=f"Unsupported checksum algorithm '{algorithm}'"):
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


# --- filename sanitising (details in test_util.py) ---------------------------------

@pytest.mark.parametrize(
    ("filename", "mimetype", "expected"),
    [
        ("Bescheid.pdf", "application/pdf", "bescheid.pdf"),
        ("Rechnung März 2026.pdf", "application/pdf", "rechnung_marz_2026.pdf"),
        ("mailbody", "text/plain", "mailbody.txt"),
        ("a   b.pdf", "application/pdf", "a_b.pdf"),
    ],
)
def test_sanitized_filename(mailbox, service, filename, mimetype, expected):
    service.add_delivery(attachments=[FakeAttachment(b"x", filename, mimetype)])

    [delivery] = mailbox.deliveries()
    [attachment] = delivery.attachments()

    assert attachment.sanitized_filename == expected
    assert attachment.sanitize_fname(filename, mimetype) == expected  # compatibility alias
