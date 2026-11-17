"""In-memory fake of the Mein Postkorb service, for tests without certificate or network.

Responses are real zeep objects built from the bundled WSDL's own response
elements, so a fake that uses a field the real schema doesn't have fails at
construction, instead of drifting away from what the service actually returns.

Server behaviour follows the vendor HowTo (docs/Mein_Postkorb_AutomatischeAbholung_HowTo.txt):
unknown delivery IDs yield an ``Error`` with code ``000``, a failed attachment
download is an HTTP 400, and ``Limit`` is capped at 1000 (per the XSD).
"""

from __future__ import annotations

import datetime
import hashlib
import itertools
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import requests

from usp_mp.adapter import USP_MP_Adapter
from usp_mp.errors import MailboxStateError

SERVER_MAX_LIMIT = 1000
UNKNOWN_UUID_ERROR = ("000", "Unkown uuid.")  # sic, as documented by the vendor
BASE_TIMESTAMP = datetime.datetime(2026, 1, 1, 8, 0, tzinfo=datetime.timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass
class FakeAttachment:
    content: bytes
    filename: str = "document.pdf"
    mimetype: str = "application/pdf"
    algorithm: str = "SHA256"  # schema allows SHA256 or SHA512
    checksum: bytes | None = None  # None: computed from content; set it to simulate corruption
    attachment_id: str = field(default_factory=_new_id)

    def checksum_value(self) -> bytes:
        if self.checksum is not None:
            return self.checksum
        return hashlib.new(self.algorithm, self.content).digest()


@dataclass
class FakeDelivery:
    subject: str
    attachments: list[FakeAttachment]
    timestamp: datetime.datetime
    read: bool = False
    delivery_id: str = field(default_factory=_new_id)


class FakeService:
    """Server side of the fake: holds the mailbox and answers the four SOAP operations.

    Called exactly like zeep's ``ServiceProxy`` (``service.GetDelivery(id, Version=...)``),
    and also serves the REST attachment download. Every call is recorded in ``calls``.
    """

    def __init__(self, wsdl_client) -> None:
        self._wsdl = wsdl_client
        self.deliveries: dict[str, FakeDelivery] = {}
        self.calls: list[tuple] = []
        self.open_connections = 0
        self._errors: dict[str, tuple[str, str]] = {}
        self._counter = itertools.count(1)

    # --- test setup ---------------------------------------------------------------

    def add_delivery(
        self,
        subject: str | None = None,
        attachments: list[FakeAttachment] | None = None,
        *,
        read: bool = False,
    ) -> FakeDelivery:
        n = next(self._counter)
        if attachments is None:
            # Every real delivery carries at least the text/plain mail body
            attachments = [FakeAttachment(b"Mail body %d\n" % n, "mailbody", "text/plain")]
        delivery = FakeDelivery(
            subject=subject or f"Delivery {n}",
            attachments=attachments,
            timestamp=BASE_TIMESTAMP + datetime.timedelta(minutes=n),
            read=read,
        )
        self.deliveries[delivery.delivery_id] = delivery
        return delivery

    def add_deliveries(self, count: int, **kwargs) -> list[FakeDelivery]:
        return [self.add_delivery(**kwargs) for _ in range(count)]

    def fail_next(self, operation: str, code: str = "999", message: str = "Simulated error") -> None:
        """Make the next call of `operation` return an API ``Error`` response."""
        self._errors[operation] = (code, message)

    def calls_to(self, operation: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == operation]

    # --- helpers ------------------------------------------------------------------

    def _response(self, element: str, **values):
        return self._wsdl.get_element(f"ns0:{element}")(**values)

    def _error_response(self, element: str, code: str, message: str):
        return self._response(element, Error={"ErrorCode": code, "ErrorMessage": message})

    def _injected_error(self, operation: str):
        if operation in self._errors:
            code, message = self._errors.pop(operation)
            return self._error_response(f"{operation}Response", code, message)
        return None

    # --- SOAP operations ------------------------------------------------------------

    def QueryDeliveries(self, selector, Version=None):
        self.calls.append(("QueryDeliveries", selector, Version))
        if (err := self._injected_error("QueryDeliveries")) is not None:
            return err

        kind = selector._xsd_type.name
        if kind == "AllDeliveriesType":
            matching = list(self.deliveries)
            start, limit = selector.Paging.Start or 0, selector.Paging.Limit
        elif kind == "NewDeliveriesOnlyType":
            matching = [i for i, d in self.deliveries.items() if not d.read]
            start, limit = 0, selector.Limit
        else:
            raise AssertionError(f"unexpected query selector {kind}")

        limit = min(limit or 100, SERVER_MAX_LIMIT)
        return self._response(
            "QueryDeliveriesResponse",
            ResultsList={"HitCount": len(matching), "DeliveryID": matching[start:start + limit]},
        )

    def GetDelivery(self, delivery_id, Version=None):
        self.calls.append(("GetDelivery", delivery_id, Version))
        if (err := self._injected_error("GetDelivery")) is not None:
            return err
        d = self.deliveries.get(delivery_id)
        if d is None:
            return self._error_response("GetDeliveryResponse", *UNKNOWN_UUID_ERROR)

        return self._response("GetDeliveryResponse", Delivery={
            "DeliveryID": d.delivery_id,
            "MetaData": {"DeliveryTimestamp": d.timestamp, "Subject": d.subject},
            "AttachmentList": {"Attachment": [
                {
                    "AttachmentID": a.attachment_id,
                    "FileName": a.filename,
                    "MimeType": a.mimetype,
                    "Size": len(a.content),
                    "CheckSum": {"AlgorithmID": a.algorithm, "Value": a.checksum_value()},
                }
                for a in d.attachments
            ]},
        })

    def CloseDelivery(self, delivery_id, Version=None):
        self.calls.append(("CloseDelivery", delivery_id, Version))
        if (err := self._injected_error("CloseDelivery")) is not None:
            return err
        d = self.deliveries.get(delivery_id)
        if d is None:
            return self._error_response("CloseDeliveryResponse", *UNKNOWN_UUID_ERROR)
        d.read = True
        return self._response("CloseDeliveryResponse", Success=True)

    def DeleteDelivery(self, delivery_id, Version=None):
        self.calls.append(("DeleteDelivery", delivery_id, Version))
        if (err := self._injected_error("DeleteDelivery")) is not None:
            return err
        if self.deliveries.pop(delivery_id, None) is None:
            return self._error_response("DeleteDeliveryResponse", *UNKNOWN_UUID_ERROR)
        return self._response("DeleteDeliveryResponse", Success=True)

    # --- REST attachment download ---------------------------------------------------

    def download_attachment(self, delivery_id: str, attachment_id: str) -> bytes:
        self.calls.append(("download_attachment", delivery_id, attachment_id))
        d = self.deliveries.get(delivery_id)
        for a in d.attachments if d else ():
            if a.attachment_id == attachment_id:
                return a.content
        response = requests.Response()
        response.status_code = 400
        raise requests.HTTPError("400 Client Error: Bad Request", response=response)


class FakeConnection:
    """Stands in for ``USP_MP_Adapter.ConnectionContext``, including its closed state."""

    def __init__(self, adapter: FakeAdapter) -> None:
        self.uspmp = adapter
        self._service = adapter.service
        self.closed = False

    def _ensure_open(self) -> None:
        if self.closed:
            raise MailboxStateError("Mailbox connection is closed")

    @property
    def service(self) -> FakeService:
        self._ensure_open()
        return self._service

    def __enter__(self):
        self._ensure_open()
        self._service.open_connections += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._service.open_connections -= 1

    def download_attachment(self, delivery_id: str, attachment_id: str) -> bytes:
        self._ensure_open()
        return self._service.download_attachment(delivery_id, attachment_id)


class FakeAdapter:
    """Stands in for ``USP_MP_Adapter``: same constructor, VERSION, types and connection()."""

    VERSION = USP_MP_Adapter.VERSION

    def __init__(self, service: FakeService, wsdl_client, p12_path, p12_pass=None, *, prod=False) -> None:
        self.service = service
        self.p12_path = p12_path
        self.p12_pass = p12_pass
        self.prod = prod
        self.connections_opened = 0
        # Real zeep types, so selectors are built exactly as against the real service
        self.types = SimpleNamespace(**{
            name: wsdl_client.get_type(f"ns0:{name}")
            for name in ("AllDeliveriesType", "PagingType", "NewDeliveriesOnlyType")
        })

    def connection(self) -> FakeConnection:
        self.connections_opened += 1
        return FakeConnection(self)
