
from collections.abc import Iterator
from typing import Any
import re
import os.path
import mimetypes
import hashlib
from datetime import datetime
from .adapter import USP_MP_Adapter

from zeep.proxy import ServiceProxy

class Mailbox:
    uspmp: USP_MP_Adapter|None = None

    def __init__(self, p12_path: str, p12_pass: str, prod: bool=False) -> None:
        try:
            self.uspmp = USP_MP_Adapter(p12_path, p12_pass, prod=prod)
        except Exception as e:
            raise RuntimeError(f"Failed to initialize USP_MP_Adapter: {e}")

    def deliveries(self, all: bool=True):
        assert self.uspmp is not None, "USP_MP_Adapter not initialised"

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
            if r.ResultsList.HitCount == 0:
                return

            for delivery_id in r.ResultsList.DeliveryID:
                yield Delivery(conn, delivery_id)

class Attachment:
    def __init__(self, connection: USP_MP_Adapter.ConnectionContext, delivery_id: str, att: Any) -> None:
        self.delivery_id = delivery_id
        self.connection = connection
        self.attachment = att

    def __str__(self) -> str:
        return f"Attachment<{self.id} [{self.filename}]>"

    SANITIZE_RE = re.compile('[^a-z0-9_-]', re.IGNORECASE)
    def sanitize_fname(self, s: str, mime: str) -> str:
        fpart, extpart = os.path.splitext(s)
        n = self.SANITIZE_RE.sub('_', fpart.lower()).replace('__', '_')
        guessed_ext = mimetypes.guess_extension(mime, strict=False)
        return ''.join((n, guessed_ext if guessed_ext else extpart))

    def content(self, verify_checksum=True) -> bytes:
        content_bytes = self.connection.download_attachment(self.delivery_id, self.id)

        if verify_checksum:
            hash = hashlib.new(self.hash[0])
            if hash is None:
                raise KeyError(f"Could not instanciate hash algorithm {self.hash[0]}")
            hash.update(content_bytes)

            if hash.digest() != self.hash[1]:
                raise ValueError(f"Downloaded content bytes do not match checksum (expected {self.hash[1].hex()}, got {hash.hexdigest()})")

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

class Delivery:
    def __init__(self, connection: USP_MP_Adapter.ConnectionContext, id: str) -> None:
        self.id = id
        self.connection = connection
        assert connection.service
        self.delivery = connection.service.GetDelivery(id, Version=connection.uspmp.VERSION)

    def __str__(self) -> str:
        return f"Delivery<{self.id} [{self.subject}]>"

    @property
    def delivery_ts(self) -> datetime:
        return self.delivery.Delivery.MetaData.DeliveryTimestamp

    @property
    def subject(self) -> str:
        return self.delivery.Delivery.MetaData.Subject

    """
    'MetaData': {
            'AppDeliveryID': '00000000-0000-0000-0000-000000000000',
            'DeliveryService': 'MeinPostfach Zustelldienst',
            'ZSDeliveryID': '00000000-0000-0000-0000-000000000000',
            'Origin': {
                'ParticipantID': 'AT:XXX:12345'
            },
            'DeliveryTimestamp': datetime.datetime(2020, 1, 1, 0, 0, 0, 0, tzinfo=<FixedOffset '+01:00'>),
            'Subject': 'Some Subject',
            'GZ': 'GU_123123123123',
            'DeliveryQuality': 'nonRSa',
            'PrivateMessageQuality': None,
            'Reference': None,
            'Tags': None,
            'MetadataList': None
        },
    """

    def attachments(self) -> Iterator[Attachment]:
        delivery = self.delivery.Delivery
        all_attachments = delivery.AttachmentList.Attachment
        if not all_attachments:
            return

        for att in all_attachments:
            yield Attachment(self.connection, delivery.DeliveryID, att)
