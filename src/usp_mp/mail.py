# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import date, datetime

from lxml import etree  # pyright: ignore[reportAttributeAccessIssue]  # compiled module, no stubs
from zeep.xsd.valueobjects import CompoundValue

from .adapter import USP_MP_Adapter
from .errors import ApiError, ChecksumError, MailboxStateError
from .util import sanitize_filename

logger = logging.getLogger(__name__)

# Largest Limit the service accepts in one QueryDeliveries call (zuseaa_p2.xsd: 1..1000)
PAGE_SIZE = 1000


def raise_on_error(response: Any, operation: str) -> None:
    """Raise ApiError if a SOAP response carries an ``Error``.

    Every response type is a choice of result or ``Error``, and an ``Error``
    always has both ErrorCode and ErrorMessage (zuseaa_p2.xsd).
    """
    error = getattr(response, "Error", None)
    if error is not None:
        raise ApiError(error.ErrorCode, error.ErrorMessage, operation)


def unparsed_elements(value: Any) -> list[str]:
    """Names of XML elements that zeep couldn't map to the schema, anywhere in `value`.

    With strict=False, zeep keeps elements the schema doesn't describe as raw
    lxml elements in `_raw_elements` instead of failing.
    """
    found: list[str] = []

    def walk(v: Any) -> None:
        if isinstance(v, CompoundValue):
            for key, item in v.__values__.items():
                if key == "_raw_elements":
                    found.extend(etree.QName(e).localname for e in item or [])
                else:
                    walk(item)
        elif isinstance(v, list):
            for item in v:
                walk(item)

    walk(value)
    return found


def _text(value: Any) -> str | None:
    """Text of an XSD simple-content element (zeep puts it in `_value_1` when the element has attributes)."""
    if value is None:
        return None
    return getattr(value, "_value_1", value)


@dataclass(frozen=True)
class Contact:
    """Sender or receiver of a delivery (aa:ContactType): an organisation or a natural person."""

    kind: str | None
    """"corporate_body", "physical_person", or None if the service sent neither."""
    name: str | None
    """FullName of an organisation, or "GivenName FamilyName" of a person."""
    organization: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    date_of_birth: date | None = None
    identification_type: str | None = None
    """e.g. "urn:publicid:gv.at:baseid+XGLN" (GLN) or "...+XFN" (company register number)."""
    identification_value: str | None = None

    @classmethod
    def from_zeep(cls, contact: Any) -> Contact | None:
        if contact is None:
            return None
        ident = contact.Identification
        common = {
            "identification_type": None if ident is None else ident.Type,
            "identification_value": None if ident is None else _text(ident.Value),
        }
        if contact.CorporateBody is not None:
            body = contact.CorporateBody
            return cls(kind="corporate_body", name=body.FullName, organization=body.Organization, **common)
        if contact.PhysicalPerson is not None:
            person = contact.PhysicalPerson
            given = person.Name.GivenName if person.Name is not None else None
            family = _text(person.Name.FamilyName) if person.Name is not None else None
            name = " ".join(part for part in (given, family) if part) or None
            return cls(kind="physical_person", name=name, given_name=given, family_name=family,
                       date_of_birth=person.DateOfBirth, **common)
        return cls(kind=None, name=None, **common)


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

    def deliveries(self, all: bool = True, *, limit: int | None = None) -> Iterator[Delivery]:
        """Yield the mailbox's deliveries: all of them, or only new (unread) ones.

        The delivery IDs are queried when this is called; each Delivery is then
        fetched as the iterator reaches it. Because all IDs are collected up
        front, closing or deleting deliveries while iterating is safe.

        all=True pages through the whole mailbox. all=False can't be paged (the
        service only offers a limit), so it returns at most 1000 deliveries and
        logs a warning if there are more: close the processed ones and query
        again. `limit` caps the number of deliveries returned.
        """
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, not {limit}")
        conn = self.connection  # fails right here if the mailbox isn't open
        ids = self._all_ids(conn, limit) if all else self._new_ids(conn, limit)
        return (Delivery(conn, delivery_id) for delivery_id in ids)

    def _query(self, conn: USP_MP_Adapter.ConnectionContext, selector) -> tuple[int, list[str]]:
        r = conn.service.QueryDeliveries(selector, Version=self.uspmp.VERSION)
        raise_on_error(r, "QueryDeliveries")
        hit_count, ids = r.ResultsList.HitCount, list(r.ResultsList.DeliveryID or [])
        paging = getattr(selector, "Paging", None)
        logger.debug(
            "QueryDeliveries %s%s: HitCount %d, %d IDs returned",
            selector._xsd_type.name,
            f" Start={paging.Start} Limit={paging.Limit}" if paging is not None else f" Limit={selector.Limit}",
            hit_count, len(ids),
        )
        return hit_count, ids

    def _all_ids(self, conn: USP_MP_Adapter.ConnectionContext, limit: int | None) -> list[str]:
        types = self.uspmp.types
        ids: dict[str, None] = {}  # ordered set
        start = 0
        while limit is None or len(ids) < limit:
            page_size = PAGE_SIZE if limit is None else min(PAGE_SIZE, limit - len(ids))
            selector = types.AllDeliveriesType(Paging=types.PagingType(Start=start, Limit=page_size))  # pyright: ignore[reportCallIssue]
            hit_count, page = self._query(conn, selector)
            # A delivery arriving mid-paging shifts offsets and can repeat an ID; keep the first
            ids.update(dict.fromkeys(page))
            start += len(page)
            if not page or start >= hit_count:
                break
        return list(ids)[:limit]

    def _new_ids(self, conn: USP_MP_Adapter.ConnectionContext, limit: int | None) -> list[str]:
        requested = PAGE_SIZE if limit is None else min(limit, PAGE_SIZE)
        selector = self.uspmp.types.NewDeliveriesOnlyType(Limit=requested)  # pyright: ignore[reportCallIssue]
        hit_count, ids = self._query(conn, selector)
        if hit_count > len(ids) and (limit is None or len(ids) < limit):
            # The service applies Limit to the whole mailbox (read and unread) and only
            # then picks the unread deliveries, and new-only queries have no offset
            # (observed on demo and production; tests/test_demo.py checks it). Unread
            # deliveries outside that window are unreachable this way; closing doesn't
            # move them into it, deleting older ones does. Only a warning, no automatic
            # fallback: real mailboxes hold about a dozen deliveries, far below PAGE_SIZE.
            logger.warning(
                "%d new deliveries, but only %d returned: the service looks for unread deliveries "
                "only among the first %d deliveries of the whole mailbox. Delete processed "
                "deliveries (closing is not enough), or use deliveries(all=True) and keep track "
                "of processed delivery IDs yourself",
                hit_count, len(ids), requested,
            )
        return ids


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
    def size(self) -> int | None:
        """Size in bytes as declared by the service."""
        return self.attachment.Size

    @property
    def document_class(self) -> str | None:
        return self.attachment.DocumentClass

    @property
    def raw(self) -> Any:
        """The underlying zeep attachment object (aa:AttachmentType)."""
        return self.attachment

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
        logger.debug("Fetched delivery %s", id)
        if unparsed := unparsed_elements(self.delivery):
            logger.warning(
                "GetDelivery response for %s contains elements the bundled schema doesn't "
                "describe; they are not parsed: %s", id, ", ".join(sorted(set(unparsed))),
            )

    def __repr__(self) -> str:
        return f"Delivery<{self.id} [{self.subject}]>"

    @property
    def delivery_ts(self) -> datetime:
        return self.delivery.Delivery.MetaData.DeliveryTimestamp

    @property
    def subject(self) -> str:
        return self.delivery.Delivery.MetaData.Subject

    @property
    def raw(self) -> Any:
        """The underlying zeep delivery object (aa:DeliveryType), for fields without an accessor."""
        return self.delivery.Delivery

    @property
    def sender(self) -> Contact | None:
        return Contact.from_zeep(self.raw.Sender)

    @property
    def receiver(self) -> Contact | None:
        return Contact.from_zeep(self.raw.Receiver)

    @property
    def app_delivery_id(self) -> str | None:
        """The sending application's ID for this delivery."""
        return self.raw.MetaData.AppDeliveryID

    @property
    def zs_delivery_id(self) -> str | None:
        """The delivery system's (Zustellsystem) ID for this delivery."""
        return self.raw.MetaData.ZSDeliveryID

    @property
    def delivery_service(self) -> str | None:
        return self.raw.MetaData.DeliveryService

    @property
    def gz(self) -> str | None:
        """Geschäftszahl (the sender's file reference), if given."""
        return self.raw.MetaData.GZ

    @property
    def origin(self) -> str | None:
        """ParticipantID of the system the delivery came from."""
        origin = self.raw.MetaData.Origin
        return None if origin is None else origin.ParticipantID

    @property
    def delivery_quality(self) -> str | None:
        """DeliveryQuality (e.g. "nonRSa", "RSa", "RSb") or PrivateMessageQuality, whichever is set."""
        meta = self.raw.MetaData
        return meta.DeliveryQuality if meta.DeliveryQuality is not None else meta.PrivateMessageQuality

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
        logger.info("Closed (marked as read) delivery %s", self.id)

        if delete:
            self.delete()

    def delete(self):
        r = self.connection.service.DeleteDelivery(
            self.id, Version=self.connection.uspmp.VERSION
        )
        raise_on_error(r, "DeleteDelivery")
        logger.info("Deleted delivery %s", self.id)
