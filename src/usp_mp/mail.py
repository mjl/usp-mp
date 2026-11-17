from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import hashlib
import hmac
from datetime import datetime
from .adapter import USP_MP_Adapter
from .errors import ApiError, ChecksumError, MailboxStateError
from .util import sanitize_filename


def raise_on_error(response: Any, operation: str) -> None:
    """Raise ApiError if a SOAP response carries an ``Error``.

    Every response type is a choice of result or ``Error``, and an ``Error``
    always has both ErrorCode and ErrorMessage (zuseaa_p2.xsd).
    """
    error = getattr(response, "Error", None)
    if error is not None:
        raise ApiError(error.ErrorCode, error.ErrorMessage, operation)


class Mailbox:
    """A Mein Postkorb mailbox. Must be used as a context manager:

        with Mailbox(p12_path, p12_pass) as mbox:
            for delivery in mbox.deliveries():
                ...

    Constructing it only loads the client certificate; entering it opens the
    connection, leaving it closes the connection. Deliveries and attachments
    obtained inside the block can't contact the service after it ends.
    A Mailbox can be opened only once.
    """

    def __init__(self, p12_path: str, p12_pass: str, prod: bool = False) -> None:
        # Raises CertificateError for an unreadable/undecryptable .p12
        self.uspmp = USP_MP_Adapter(p12_path, p12_pass, prod=prod)
        self._connection: USP_MP_Adapter.ConnectionContext | None = None
        self._used = False

    def __enter__(self) -> Mailbox:
        if self._used:
            raise MailboxStateError("A Mailbox can only be opened once; create a new one")
        self._used = True
        self._connection = self.uspmp.connection().__enter__()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def close(self) -> None:
        """Close the connection. Safe to call more than once."""
        self._used = True
        if self._connection is not None:
            self._connection.close()

    @property
    def connection(self) -> USP_MP_Adapter.ConnectionContext:
        if self._connection is None:
            if self._used:
                raise MailboxStateError("Mailbox is closed")
            raise MailboxStateError("Mailbox is not open; use it as 'with Mailbox(...) as mbox:'")
        if self._connection.closed:
            raise MailboxStateError("Mailbox is closed")
        return self._connection

    @property
    def service(self):
        """The zeep service proxy, for calling SOAP operations directly."""
        return self.connection.service

    def deliveries(self, all: bool = True) -> Iterator[Delivery]:
        # Not a generator itself, so that using a closed/unopened mailbox fails right here
        return self._deliveries(self.connection, all)

    def _deliveries(self, conn: USP_MP_Adapter.ConnectionContext, all: bool) -> Iterator[Delivery]:
        if all:
            deliveries_selector = self.uspmp.types.AllDeliveriesType(
                Paging=self.uspmp.types.PagingType(Start=0, Limit=100)  # pyright: ignore[reportCallIssue]
            )
        else:
            deliveries_selector = self.uspmp.types.NewDeliveriesOnlyType(Limit=100)  # pyright: ignore[reportCallIssue]

        r = conn.service.QueryDeliveries(deliveries_selector, Version=self.uspmp.VERSION)
        raise_on_error(r, "QueryDeliveries")
        if r.ResultsList.HitCount == 0:
            return

        for delivery_id in r.ResultsList.DeliveryID:
            yield Delivery(conn, delivery_id)


class Attachment:
    def __init__(
        self, connection: USP_MP_Adapter.ConnectionContext, delivery_id: str, att: Any
    ) -> None:
        self.delivery_id = delivery_id
        self.connection = connection
        self.attachment = att

    def __repr__(self) -> str:
        return f"Attachment<{self.id} [{self.filename}]>"

    def sanitize_fname(self, s: str, mime: str) -> str:
        """Kept for compatibility; use `usp_mp.util.sanitize_filename`."""
        return sanitize_filename(s, mime)

    # CheckSumType/AlgorithmID enumeration in zuse_p2.xsd -> hashlib name
    CHECKSUM_ALGORITHMS = {"SHA256": "sha256", "SHA512": "sha512"}

    def content(self, verify_checksum=True) -> bytes:
        """Download content bytes for attachment, and check hash value (optional)"""

        content_bytes = self.connection.download_attachment(self.delivery_id, self.id)

        if verify_checksum:
            algorithm, expected = self.hash
            hashlib_name = self.CHECKSUM_ALGORITHMS.get(algorithm)
            if hashlib_name is None:
                raise ChecksumError(
                    f"Unsupported checksum algorithm {algorithm!r} for attachment {self.id} "
                    f"(expected one of {', '.join(self.CHECKSUM_ALGORITHMS)})"
                )
            actual = hashlib.new(hashlib_name, content_bytes).digest()

            if not hmac.compare_digest(actual, expected):
                raise ChecksumError(
                    f"Downloaded content bytes do not match checksum "
                    f"(expected {expected.hex()}, got {actual.hex()})"
                )

        return content_bytes

    @property
    def id(self) -> str:
        return self.attachment.AttachmentID

    @property
    def filename(self) -> str:
        return self.attachment.FileName

    @property
    def mimetype(self) -> str:
        return self.attachment.MimeType

    @property
    def sanitized_filename(self) -> str:
        return sanitize_filename(self.filename, self.mimetype)

    @property
    def hash(self) -> tuple[str, bytes]:
        return self.attachment.CheckSum.AlgorithmID, self.attachment.CheckSum.Value


class Delivery:
    def __init__(self, connection: USP_MP_Adapter.ConnectionContext, id: str) -> None:
        self.id = id
        self.connection = connection
        self.delivery = connection.service.GetDelivery(
            id, Version=connection.uspmp.VERSION
        )
        raise_on_error(self.delivery, "GetDelivery")

    def __repr__(self) -> str:
        return f"Delivery<{self.id} [{self.subject}]>"

    @property
    def delivery_ts(self) -> datetime:
        return self.delivery.Delivery.MetaData.DeliveryTimestamp

    @property
    def subject(self) -> str:
        return self.delivery.Delivery.MetaData.Subject

    def attachments(self) -> Iterator[Attachment]:
        delivery = self.delivery.Delivery
        all_attachments = delivery.AttachmentList.Attachment
        if not all_attachments:
            return

        for att in all_attachments:
            yield Attachment(self.connection, delivery.DeliveryID, att)

    def close(self, delete=False):
        r = self.connection.service.CloseDelivery(
            self.id, Version=self.connection.uspmp.VERSION
        )
        raise_on_error(r, "CloseDelivery")

        if delete:
            self.delete()

    def delete(self):
        r = self.connection.service.DeleteDelivery(
            self.id, Version=self.connection.uspmp.VERSION
        )
        raise_on_error(r, "DeleteDelivery")
