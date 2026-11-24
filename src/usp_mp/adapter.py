# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

import datetime
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any
import ssl
import tempfile
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.backends import default_backend

from zeep import Client, Settings

from .errors import CertificateError, MailboxStateError

logger = logging.getLogger(__name__)

# Warn this long before the client certificate expires
CERTIFICATE_EXPIRY_WARNING = datetime.timedelta(days=30)
from zeep.transports import Transport
from zeep.wsdl import Document


class SSLContextAdapter(HTTPAdapter):
    """requests transport adapter that uses a preconfigured SSLContext.

    The context already carries the client certificate and the trusted CA,
    so no certificate or key files are needed while the session is in use.
    """

    def __init__(self, ssl_context: ssl.SSLContext, **kwargs) -> None:
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = self._ssl_context
        return super().init_poolmanager(*args, **kwargs)

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        # Without this, an HTTPS_PROXY setting would bypass the client certificate.
        proxy_kwargs["ssl_context"] = self._ssl_context
        return super().proxy_manager_for(proxy, **proxy_kwargs)


def _write_private_file(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)


@dataclass(frozen=True)
class QueryTypes:
    """zeep types for building QueryDeliveries selectors, resolved from the bundled WSDL."""

    AllDeliveriesType: Any
    PagingType: Any
    NewDeliveriesOnlyType: Any


class USP_MP_Adapter:
    """Certificate, TLS context and parsed WSDL for one Mein Postkorb endpoint.

    Not thread-safe: a connection's requests.Session and zeep client must not be
    shared between threads. Use one Mailbox (and so one adapter) per thread.
    """

    DEMO_ENDPOINT_URL = "https://demo-autoabholung.meinpostkorb.brz.gv.at"
    PROD_ENDPOINT_URL = "https://autoabholung.meinpostkorb.brz.gv.at"

    VERSION = "2.4.0-004"
    WSDL_PATH = os.path.join(os.path.dirname(__file__), 'wsdl', 'zuseaa_p2.wsdl')
    CA_PATH = os.path.join(os.path.dirname(__file__), 'certs', 'brz_ca.cer')
    TIMEOUT = 10

    def __init__(self, p12_path: str, p12_pass: str|None=None, *, prod: bool=False) -> None:
        self.endpoint = self.PROD_ENDPOINT_URL if prod else self.DEMO_ENDPOINT_URL
        self.soap_endpoint = urljoin(self.endpoint, 'soap')
        self.attachment_endpoint = urljoin(self.endpoint, 'attachment')

        from . import __version__
        self.headers = {'User-Agent': f'USP_MP/{__version__}'}

        logger.debug("Using %s endpoint %s", "production" if prod else "demo", self.endpoint)

        private_key, cert = self.load_p12_cert(p12_path, p12_pass)
        self.certificate_expires = self.check_expiry(cert)
        self.ssl_context = self.build_ssl_context(private_key, cert)
        del private_key  # only the SSLContext keeps the key from here on

        # Parsed once per adapter; every connection's zeep client reuses it
        self.settings = self.zeep_settings(self.headers)
        self.wsdl, self.types = self.load_wsdl(self.settings)

    @staticmethod
    def zeep_settings(headers: dict[str, str]) -> Settings:
        # The bundled schema is patched so that zeep can parse the p:Person substitution
        # (PhysicalPerson/CorporateBody) in Sender/Receiver; see wsdl/PATCHES.md. With the
        # patch, all demo responses also parse with strict=True (tests/test_demo.py checks
        # that). strict=False is kept deliberately: if the service ever sends an element the
        # bundled schema doesn't know, retrieval keeps working, the element ends up in
        # _raw_elements, and Delivery logs a warning instead of failing.
        # zeep reads `strict` from the settings the WSDL Document was parsed with.
        return Settings(strict=False, extra_http_headers=headers)  # pyright: ignore[reportCallIssue]

    @classmethod
    def load_wsdl(cls, settings: Settings | None = None) -> tuple[Document, QueryTypes]:
        """Parse the bundled WSDL (the service doesn't serve ?wsdl) and resolve the query types."""
        client = Client(wsdl=cls.WSDL_PATH, settings=settings)  # local files only, no network
        if not client.wsdl.services:
            raise ValueError("No services found in WSDL")
        types = QueryTypes(**{
            name: client.get_type(f'ns0:{name}')
            for name in ('AllDeliveriesType', 'PagingType', 'NewDeliveriesOnlyType')
        })
        return client.wsdl, types

    def load_p12_cert(self, p12_path: str, p12_pass: str|None = None):
        """Load PKCS12 and extract certificate and private key; raises CertificateError"""

        try:
            with open(p12_path, "rb") as f:
                p12_data = f.read()
        except OSError as e:
            raise CertificateError(f"Cannot read client certificate {p12_path}: {e.strerror}") from e
        password = p12_pass.encode() if p12_pass else None
        try:
            private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
                p12_data, password, default_backend()
            )
        except ValueError as e:
            raise CertificateError(
                f"Cannot load client certificate {p12_path}: wrong password or not a PKCS#12 file"
            ) from e
        if private_key is None or certificate is None:
            raise CertificateError(f"Client certificate {p12_path} contains no private key and certificate")
        return private_key, certificate

    @staticmethod
    def check_expiry(certificate) -> datetime.datetime:
        """Log when the client certificate expires; warn shortly before (or after) it does."""
        expires = getattr(certificate, "not_valid_after_utc", None)  # cryptography >= 42
        if expires is None:
            expires = certificate.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        remaining = expires - datetime.datetime.now(datetime.timezone.utc)
        if remaining <= datetime.timedelta(0):
            logger.warning("Client certificate expired on %s; the service will reject it", expires.date())
        elif remaining <= CERTIFICATE_EXPIRY_WARNING:
            logger.warning("Client certificate expires on %s (in %d days); create a new one in USP",
                           expires.date(), remaining.days)
        else:
            logger.debug("Client certificate valid until %s", expires.date())
        return expires

    def build_ssl_context(self, private_key, certificate) -> ssl.SSLContext:
        """Build a client SSLContext carrying the client certificate and the BRZ CA.

        Python's ssl module can only load keys from files, so the key is written
        to a private temporary directory -- but only in encrypted form, with a
        random one-time password that never leaves this process. The directory
        is removed as soon as the key is loaded, even if loading fails.
        """
        # Same defaults requests/urllib3 would use (incl. VERIFY_X509_STRICT on 3.13+)
        ctx = create_urllib3_context()
        ctx.load_verify_locations(cafile=self.CA_PATH)

        one_time_password = secrets.token_urlsafe(32).encode()
        cert_pem = certificate.public_bytes(encoding=serialization.Encoding.PEM)
        encrypted_key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.BestAvailableEncryption(one_time_password),
        )

        with tempfile.TemporaryDirectory(prefix="usp_mp-") as tmpdir:  # created with mode 0700
            cert_path = os.path.join(tmpdir, "cert.pem")
            key_path = os.path.join(tmpdir, "key.pem")
            _write_private_file(cert_path, cert_pem)
            _write_private_file(key_path, encrypted_key_pem)
            try:
                ctx.load_cert_chain(cert_path, key_path, password=one_time_password)
            except ssl.SSLError as e:
                raise CertificateError(f"Cannot use client certificate: {e}") from e

        return ctx

    class ConnectionContext:
        """One HTTP session and zeep client. Usable until it is exited/closed."""

        def __init__(self, uspmp):
            self.uspmp = uspmp
            self.closed = False
            self.session = requests.Session()
            # The mounted context does the actual verification; keeping `verify`
            # pinned to the BRZ CA ensures nothing falls back to the system store.
            self.session.verify = uspmp.CA_PATH
            self.session.mount("https://", SSLContextAdapter(uspmp.ssl_context))

            transport = Transport(session=self.session, timeout=uspmp.TIMEOUT, operation_timeout=uspmp.TIMEOUT)
            # After Transport(): zeep overwrites the session's User-Agent with its own
            self.session.headers.update(uspmp.headers)

            # Reuses the adapter's parsed WSDL; SOAP calls go through this connection's transport
            self.client = Client(wsdl=uspmp.wsdl, transport=transport, settings=uspmp.settings)
            binding = next(iter(self.client.wsdl.bindings))  # load_wsdl checked there is one
            self._service = self.client.create_service(binding, uspmp.soap_endpoint)
            logger.debug("Opened connection to %s", uspmp.endpoint)

        def _ensure_open(self) -> None:
            if self.closed:
                raise MailboxStateError("Mailbox connection is closed")

        @property
        def service(self):
            """The zeep service proxy for SOAP calls."""
            self._ensure_open()
            return self._service

        def __enter__(self):
            self._ensure_open()
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.close()

        def close(self) -> None:
            if not self.closed:
                self.closed = True
                self.session.close()
                logger.debug("Closed connection to %s", self.uspmp.endpoint)

        def download_attachment(self, delivery_id: str, attachment_id: str) -> bytes:
            self._ensure_open()
            # Must go through the session: the client certificate lives in its SSLContext
            r = self.session.get(self.uspmp.attachment_endpoint,
                params={'delivery_id': delivery_id, 'attachment_id': attachment_id},
                timeout=self.uspmp.TIMEOUT,
            )
            r.raise_for_status()
            logger.debug("Downloaded attachment %s of delivery %s: %d bytes", attachment_id, delivery_id, len(r.content))
            return r.content

    def connection(self):
        """Open a new connection; use as a context manager. Mailbox does this for you."""
        return self.ConnectionContext(self)
