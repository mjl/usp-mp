"""Integration tests against the Mein Postkorb **demo** (TEST) service.

The client certificate and its password are NOT part of this repository.
Whoever runs these tests provides their own, issued for their USP account
(see docs/howto.md, "Certificate setup checklist"). Without one, every test
here is skipped. Configure it with options or environment variables; see
tests/conftest.py and the README section "Tests against the demo service":

    pytest -m demo --p12 /path/to/your.p12 --p12-password-file /path/to/password.txt
    USP_MP_P12=/path/to/your.p12 USP_MP_P12_PASS_FILE=/path/to/password.txt pytest -m demo

Always runs against the demo endpoint, never production; the `demo_mailbox`
fixture refuses anything else. Close/delete are exercised on the demo only,
where the vendor HowTo states they succeed without changing the mock data.

The demo always returns the same mock data, which is checked in three layers:

1. Invariants the schema and HowTo guarantee (HitCount, paging, checksums, ...),
   which hold for any data.
2. The exact values the vendor HowTo documents (delivery IDs, the attachment
   download example).
3. A golden snapshot of everything else (subjects, timestamps, attachments with
   sizes and checksums) in tests/data/demo_snapshot.json. If BRZ refreshes the
   demo data, re-record it with --update-demo-snapshot and review the diff.
"""

import datetime
import json
import re
import uuid
from pathlib import Path

import pytest
import requests
from zeep import Client, Settings
from zeep.exceptions import Fault, XMLParseError
from zeep.transports import Transport

from snapshot import differences
from usp_mp import cli
from usp_mp.adapter import USP_MP_Adapter
from usp_mp.errors import ApiError, ChecksumError, MailboxStateError
from usp_mp.mail import Attachment, Delivery, Mailbox

pytestmark = pytest.mark.demo

UNKNOWN_UUID = ("000", "Unkown uuid.")  # sic, as documented by the vendor
SANITIZED = re.compile(r"[a-z0-9-]+(_[a-z0-9-]+)*(\.[a-z0-9]+)?")
SNAPSHOT_PATH = Path(__file__).parent / "data" / "demo_snapshot.json"

# Demo data documented in docs/Mein_Postkorb_AutomatischeAbholung_HowTo.txt
# (sample QueryDeliveriesResponse for all and for new-only deliveries, and the
# attachment download example).
HOWTO_ALL_IDS = [
    "434b7c88-b142-11ea-8973-c35ab5612797",
    "434bcb20-b142-11ea-bd8d-c73912744e07",
    "434c18a0-b142-11ea-87f1-eb357aa289dc",
    "434c6800-b142-11ea-8da2-2bf0d7e660e0",
    "434cb5c6-b142-11ea-84ab-4fa381d9714a",
    "434d0422-b142-11ea-bd87-234fb2053efb",
    "434d5198-b142-11ea-be28-0be22abf1692",
    "434d9f54-b142-11ea-8154-2b18dc446f09",
    "434ded4c-b142-11ea-b2d4-8b5dfd297ff7",
    "434e3b9e-b142-11ea-a085-2b4acf5130c5",
]
HOWTO_NEW_IDS = [
    "434b7c88-b142-11ea-8973-c35ab5612797",
    "434cb5c6-b142-11ea-84ab-4fa381d9714a",
    "434e3b9e-b142-11ea-a085-2b4acf5130c5",
]
HOWTO_ATTACHMENT_EXAMPLE = ("434c18a0-b142-11ea-87f1-eb357aa289dc", "bae8b9aa-b141-11ea-a50d-475cdc4d30d2")


# --- fixtures -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def demo_mailbox(demo_credentials):
    mbox = Mailbox(demo_credentials.p12_path, demo_credentials.p12_pass, prod=False)
    assert mbox.uspmp.endpoint == USP_MP_Adapter.DEMO_ENDPOINT_URL, "refusing to run against non-demo endpoint"
    with mbox:
        yield mbox


class RawService:
    """Small helper around the raw SOAP service of an open mailbox."""

    def __init__(self, mbox: Mailbox) -> None:
        self.service = mbox.service
        self.version = mbox.uspmp.VERSION
        self.types = mbox.uspmp.types

    def query_all(self, start=0, limit=100):
        sel = self.types.AllDeliveriesType(Paging=self.types.PagingType(Start=start, Limit=limit))
        return self.service.QueryDeliveries(sel, Version=self.version)

    def query_new(self, limit=100):
        return self.service.QueryDeliveries(self.types.NewDeliveriesOnlyType(Limit=limit), Version=self.version)


@pytest.fixture(scope="module")
def raw(demo_mailbox):
    return RawService(demo_mailbox)


@pytest.fixture(scope="module")
def all_ids(raw):
    r = raw.query_all()
    assert r.Error is None
    return list(r.ResultsList.DeliveryID)


@pytest.fixture(scope="module")
def new_ids(raw):
    r = raw.query_new()
    assert r.Error is None
    return list(r.ResultsList.DeliveryID)


@pytest.fixture(scope="module")
def deliveries(demo_mailbox):
    result = list(demo_mailbox.deliveries(all=True))
    assert result, "demo service returned no deliveries"
    return result


@pytest.fixture(scope="module")
def attachments(deliveries):
    return [a for d in deliveries for a in d.attachments()]


def error_of(response):
    return (response.Error.ErrorCode, response.Error.ErrorMessage) if response.Error is not None else None


# --- expected data: vendor HowTo and golden snapshot -------------------------------

def test_all_ids_match_howto(all_ids):
    assert all_ids == HOWTO_ALL_IDS


def test_new_ids_match_howto(new_ids):
    assert new_ids == HOWTO_NEW_IDS


def test_howto_attachment_example_downloads(demo_mailbox):
    data = demo_mailbox.connection.download_attachment(*HOWTO_ATTACHMENT_EXAMPLE)

    assert data.startswith(b"%PDF-")


def snapshot_of(deliveries, new_ids) -> list[dict]:
    """The comparable part of the demo data. Sender/receiver are left out (unparsed, critique S4)."""
    return [
        {
            "id": d.id,
            "new": d.id in new_ids,
            "timestamp": d.delivery_ts.isoformat(),
            "subject": d.subject,
            "attachments": [
                {
                    "id": a.id,
                    "filename": a.filename,
                    "mimetype": a.mimetype,
                    "size": a.attachment.Size,
                    "checksum": {"algorithm": a.hash[0], "value": a.hash[1].hex()},
                }
                for a in d.attachments()
            ],
        }
        for d in deliveries
    ]


def test_demo_data_matches_snapshot(request, demo_mailbox, deliveries, new_ids):
    actual = snapshot_of(deliveries, set(new_ids))

    if request.config.getoption("--update-demo-snapshot"):
        SNAPSHOT_PATH.parent.mkdir(exist_ok=True)
        SNAPSHOT_PATH.write_text(json.dumps({
            "_comment": "Golden snapshot of the Mein Postkorb demo data; rewrite with "
                        "pytest -m demo --update-demo-snapshot and review the diff.",
            "recorded": datetime.date.today().isoformat(),
            "endpoint": demo_mailbox.uspmp.endpoint,
            "deliveries": actual,
        }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        pytest.skip(f"demo snapshot rewritten: {SNAPSHOT_PATH}")

    if not SNAPSHOT_PATH.exists():
        pytest.fail(f"{SNAPSHOT_PATH} is missing; record it with --update-demo-snapshot")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))["deliveries"]

    diffs = differences(expected, actual)
    assert not diffs, (
        f"demo data differs from {SNAPSHOT_PATH.name} ({len(diffs)} differences). If BRZ changed the "
        "demo data, re-record with --update-demo-snapshot and review the diff:\n  " + "\n  ".join(diffs[:30])
    )


# --- connection ---------------------------------------------------------------------

def test_targets_demo_endpoint(demo_mailbox):
    assert demo_mailbox.uspmp.soap_endpoint.startswith("https://demo-autoabholung.")


def test_client_certificate_accepted(raw):
    """TLS with the client certificate and the pinned BRZ CA works end to end."""
    assert raw.query_new(limit=1).Error is None


# --- querying -----------------------------------------------------------------------

def test_hitcount_matches_returned_ids(raw, all_ids):
    assert raw.query_all().ResultsList.HitCount == len(all_ids)


def test_delivery_ids_are_unique(all_ids):
    assert len(set(all_ids)) == len(all_ids)


def test_new_deliveries_are_subset_of_all(all_ids, new_ids):
    assert set(new_ids) <= set(all_ids)


def test_query_shape_accepted(all_ids):
    """Critique B11: zeep sends <Query xsi:type=...> instead of the HowTo's <AllDeliveries>.
    The service validates requests (see test_limit_out_of_range_rejected) and accepts it."""
    assert all_ids


@pytest.mark.parametrize("page_size", [1, 3, 4])
def test_paging_reassembles_full_list(raw, all_ids, page_size):
    pages = []
    for start in range(0, len(all_ids), page_size):
        r = raw.query_all(start=start, limit=page_size)
        assert r.ResultsList.HitCount == len(all_ids)  # HitCount is the total, not the page size
        pages.extend(r.ResultsList.DeliveryID)

    assert pages == all_ids


def test_paging_beyond_end_is_empty(raw, all_ids):
    r = raw.query_all(start=len(all_ids) + 5, limit=10)

    assert r.ResultsList.HitCount == len(all_ids)
    assert list(r.ResultsList.DeliveryID) == []


def test_new_only_is_truncated_by_limit(raw, new_ids):
    """New-only queries can't be paged: HitCount reports the total, Limit caps the IDs (critique B2)."""
    if len(new_ids) < 2:
        pytest.skip("needs at least two new deliveries in the demo data")
    r = raw.query_new(limit=1)

    assert r.ResultsList.HitCount == len(new_ids)
    assert len(r.ResultsList.DeliveryID) == 1


@pytest.mark.parametrize("limit", [0, 1001])
def test_limit_out_of_range_rejected(raw, limit):
    """The service validates requests against the schema (Limit is 1..1000)."""
    with pytest.raises(Fault, match="Validation error"):
        raw.query_all(limit=limit)


# --- library: Mailbox / Delivery ----------------------------------------------------

def test_mailbox_deliveries_match_raw_query(deliveries, all_ids):
    assert [d.id for d in deliveries] == all_ids


def test_library_pages_through_live_service(demo_mailbox, monkeypatch):
    """Mailbox.deliveries() paging loop against the real service, with tiny pages."""
    from usp_mp import mail

    monkeypatch.setattr(mail, "PAGE_SIZE", 3)

    assert [d.id for d in demo_mailbox.deliveries(all=True)] == HOWTO_ALL_IDS
    assert [d.id for d in demo_mailbox.deliveries(all=True, limit=4)] == HOWTO_ALL_IDS[:4]


def test_library_warns_when_new_only_is_truncated(demo_mailbox, monkeypatch, caplog):
    from usp_mp import mail

    monkeypatch.setattr(mail, "PAGE_SIZE", 2)
    with caplog.at_level("WARNING", logger="usp_mp.mail"):
        result = [d.id for d in demo_mailbox.deliveries(all=False)]

    # Only the first new delivery: Limit applies before the unread filter (see below)
    assert result == HOWTO_NEW_IDS[:1]
    assert "3 new deliveries, but only 1 returned" in caplog.text


@pytest.mark.parametrize(("limit", "returned"), [(1, 1), (4, 1), (5, 2), (9, 2), (10, 3)])
def test_new_only_limit_applies_before_unread_filter(raw, limit, returned):
    """Observed demo behaviour (critique B12): Limit caps the *whole* mailbox, then unread
    deliveries are picked from that window. The new ones are positions 1, 5 and 10."""
    r = raw.query_new(limit=limit)

    assert r.ResultsList.HitCount == len(HOWTO_NEW_IDS)
    assert list(r.ResultsList.DeliveryID) == HOWTO_NEW_IDS[:returned]


def test_mailbox_new_deliveries_match_raw_query(demo_mailbox, new_ids):
    assert [d.id for d in demo_mailbox.deliveries(all=False)] == new_ids


def test_delivery_metadata(deliveries):
    for d in deliveries:
        assert isinstance(d.delivery_ts, datetime.datetime)
        assert d.delivery_ts.tzinfo is not None
        assert isinstance(d.subject, str) and d.subject
        assert repr(d).startswith(f"Delivery<{d.id} ")


def test_every_delivery_starts_with_text_mail_body(deliveries):
    """howto.md: every delivery has a text/plain `mailbody` part."""
    for d in deliveries:
        first = next(d.attachments())
        assert (first.filename, first.mimetype) == ("mailbody", "text/plain")


# --- library: attachments -----------------------------------------------------------

def test_checksum_algorithms_and_values(attachments):
    """Critique B3: AlgorithmID is from the schema enumeration and Value decodes to bytes."""
    for a in attachments:
        algorithm, value = a.hash
        assert algorithm in Attachment.CHECKSUM_ALGORITHMS
        assert isinstance(value, bytes) and len(value) in (32, 64)


def test_downloads_verify_and_match_declared_size(attachments):
    for a in attachments:
        data = a.content(verify_checksum=True)
        assert len(data) == a.attachment.Size


def test_corrupted_checksum_is_detected(attachments, monkeypatch):
    a = attachments[0]
    algorithm, value = a.hash
    monkeypatch.setattr(Attachment, "hash", property(lambda self: (algorithm, bytes(len(value)))))

    with pytest.raises(ChecksumError, match="do not match checksum"):
        a.content()


def test_sanitized_filenames(deliveries):
    for d in deliveries:
        for a in d.attachments():
            assert SANITIZED.fullmatch(a.sanitized_filename), a.sanitized_filename


# --- error responses ----------------------------------------------------------------

@pytest.mark.parametrize("operation", ["GetDelivery", "CloseDelivery", "DeleteDelivery"])
def test_unknown_uuid_is_an_api_error(raw, operation):
    r = getattr(raw.service, operation)(str(uuid.uuid4()), Version=raw.version)

    assert error_of(r) == UNKNOWN_UUID


def test_unknown_delivery_raises_in_library(demo_mailbox):
    with pytest.raises(ApiError, match=r"GetDelivery failed: \[000\] Unkown uuid\."):
        Delivery(demo_mailbox.connection, str(uuid.uuid4()))


def test_unknown_attachment_is_http_400(demo_mailbox):
    with pytest.raises(requests.HTTPError) as excinfo:
        demo_mailbox.connection.download_attachment(str(uuid.uuid4()), str(uuid.uuid4()))

    assert excinfo.value.response.status_code == 400


# --- close / delete (demo only: succeed without changing the mock data) ------------

def test_close_known_delivery(raw, new_ids):
    if not new_ids:
        pytest.skip("no new deliveries in the demo data")
    r = raw.service.CloseDelivery(new_ids[0], Version=raw.version)

    assert error_of(r) is None and r.Success is True
    assert list(raw.query_new().ResultsList.DeliveryID) == new_ids  # demo ignores it


def test_delete_known_delivery(raw, all_ids):
    r = raw.service.DeleteDelivery(all_ids[-1], Version=raw.version)

    assert error_of(r) is None and r.Success is True
    assert list(raw.query_all().ResultsList.DeliveryID) == all_ids  # demo ignores it


def test_library_close_and_delete(deliveries):
    deliveries[0].close(delete=True)  # raises on an API error


# --- known schema limitation (critique S4) -----------------------------------------

@pytest.mark.xfail(strict=True, reason="critique S4: Sender/Receiver CorporateBody is left unparsed in _raw_elements")
def test_sender_and_receiver_fully_parsed(deliveries):
    for d in deliveries:
        for contact in (d.delivery.Delivery.Sender, d.delivery.Delivery.Receiver):
            assert not contact._raw_elements


@pytest.mark.xfail(strict=True, raises=XMLParseError,
                   reason="critique S4: strict parsing rejects the CorporateBody substitution")
def test_get_delivery_parses_strictly(demo_mailbox, all_ids):
    conn = demo_mailbox.connection
    client = Client(
        wsdl=USP_MP_Adapter.WSDL_PATH,
        transport=Transport(session=conn.session, timeout=USP_MP_Adapter.TIMEOUT),
        settings=Settings(strict=True),
    )
    service = client.create_service(next(iter(client.wsdl.bindings)), demo_mailbox.uspmp.soap_endpoint)

    service.GetDelivery(all_ids[0], Version=USP_MP_Adapter.VERSION)


# --- lifecycle and CLI --------------------------------------------------------------

def test_closed_mailbox_cannot_reach_service(demo_credentials):
    with Mailbox(demo_credentials.p12_path, demo_credentials.p12_pass) as mbox:
        attachment = next(next(mbox.deliveries(all=True)).attachments())

    with pytest.raises(MailboxStateError, match="closed"):
        attachment.content()


def test_cli_lists_and_saves(demo_credentials, monkeypatch, capsys, tmp_path, deliveries):
    monkeypatch.setenv("USP_MP_TEST_P12_PASS", demo_credentials.p12_pass)

    rc = cli.main([
        "--client-certificate", demo_credentials.p12_path,
        "--client-password", "ENV:USP_MP_TEST_P12_PASS",
        "--all", "--save", "--download", str(tmp_path),
    ])

    assert rc == 0
    out = capsys.readouterr().out
    assert all(d.subject in out for d in deliveries)
    saved = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert len(saved) == sum(len(list(d.attachments())) for d in deliveries)
    assert all(p.stat().st_size > 0 for p in saved)


def test_cli_rejects_wrong_password(demo_credentials, capsys):
    rc = cli.main(["--client-certificate", demo_credentials.p12_path, "--client-password", "definitely-wrong"])

    assert rc == 1
    assert "wrong password or not a PKCS#12 file" in capsys.readouterr().err
