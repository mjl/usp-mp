from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Never
import re
import os.path
import mimetypes
import hashlib
from datetime import datetime
from .adapter import USP_MP_Adapter

from zeep.proxy import ServiceProxy

class USP_MP_Base:
    def raise_on_error(self, r: Any, source: str) -> None | Never:
        if (r.Error and
            'ErrorCode' in r.Error and
            'ErrorMessage' in r.Error
        ):
            code = r.Error.ErrorCode
            msg = r.Error.ErrorMessage
            raise ValueError(f"Failed {source}: {code=} {msg=}")


class Mailbox(USP_MP_Base):

    def __init__(self, p12_path: str, p12_pass: str, prod: bool = False) -> None:
        try:
            self.uspmp = USP_MP_Adapter(p12_path, p12_pass, prod=prod)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize USP_MP_Adapter: {e}")

    def deliveries(self, all: bool = True) -> Iterator[Delivery]:
        with self.uspmp.connection() as conn:
            if not conn.service:
                raise RuntimeError("Failed to establish connection")

            service = conn.service

            if all:
                deliveries_selector = self.uspmp.types.AllDeliveriesType(
                    Paging=self.uspmp.types.PagingType(Start=0, Limit=100)  # pyright: ignore[reportCallIssue]
                )
            else:
                deliveries_selector = self.uspmp.types.NewDeliveriesOnlyType(Limit=100)  # pyright: ignore[reportCallIssue]

            r = service.QueryDeliveries(deliveries_selector, Version=self.uspmp.VERSION)
            self.raise_on_error(r, 'query deliveries')
            if r.ResultsList.HitCount == 0:
                return

            for delivery_id in r.ResultsList.DeliveryID:
                yield Delivery(conn, delivery_id)


class Attachment(USP_MP_Base):
    def __init__(
        self, connection: USP_MP_Adapter.ConnectionContext, delivery_id: str, att: Any
    ) -> None:
        self.delivery_id = delivery_id
        self.connection = connection
        self.attachment = att

    def __repr__(self) -> str:
        return f"Attachment<{self.id} [{self.filename}]>"

    SANITIZE_RE = re.compile("[^a-z0-9_-]", re.IGNORECASE)

    def sanitize_fname(self, s: str, mime: str) -> str:
        """
        Sanitize a filename `s` with mime type `mime`
        to ensure no funny characters and proper extension.
        """

        fpart, extpart = os.path.splitext(s)
        n = self.SANITIZE_RE.sub("_", fpart.lower()).replace("__", "_")
        guessed_ext = mimetypes.guess_extension(mime, strict=False)
        return "".join((n, guessed_ext if guessed_ext else extpart))

    def content(self, verify_checksum=True) -> bytes:
        """Download content bytes for attachment, and check hash value (optional)"""

        content_bytes = self.connection.download_attachment(self.delivery_id, self.id)

        if verify_checksum:
            hash = hashlib.new(self.hash[0])
            if hash is None:
                raise KeyError(f"Could not instantiate hash algorithm {self.hash[0]}")
            hash.update(content_bytes)

            if hash.digest() != self.hash[1]:
                raise ValueError(
                    f"Downloaded content bytes do not match checksum (expected {self.hash[1].hex()}, got {hash.hexdigest()})"
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
        return self.sanitize_fname(self.filename, self.mimetype)

    @property
    def hash(self) -> tuple[str, bytes]:
        return self.attachment.CheckSum.AlgorithmID, self.attachment.CheckSum.Value


class Delivery(USP_MP_Base):
    def __init__(self, connection: USP_MP_Adapter.ConnectionContext, id: str) -> None:
        self.id = id
        self.connection = connection
        assert connection.service
        self.delivery = connection.service.GetDelivery(
            id, Version=connection.uspmp.VERSION
        )
        self.raise_on_error(self.delivery, 'get delivery')

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
        assert self.connection.service
        r = self.connection.service.CloseDelivery(
            self.id, Version=self.connection.uspmp.VERSION
        )
        self.raise_on_error(r, 'close delivery')

        if delete:
            self.delete()

    def delete(self):
        assert self.connection.service
        r = self.connection.service.DeleteDelivery(
            self.id, Version=self.connection.uspmp.VERSION
        )
        self.raise_on_error(r, 'delete delivery')
