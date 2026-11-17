
import os
import secrets
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
from zeep.transports import Transport
from zeep.xsd.types.base import Type as ZeepBaseType


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


class USP_MP_Adapter:

    DEMO_ENDPOINT_URL = "https://demo-autoabholung.meinpostkorb.brz.gv.at"
    PROD_ENDPOINT_URL = "https://autoabholung.meinpostkorb.brz.gv.at"

    VERSION = "2.4.0-004"
    WSDL_PATH = os.path.join(os.path.dirname(__file__), 'wsdl', 'zuseaa_p2.wsdl')
    CA_PATH = os.path.join(os.path.dirname(__file__), 'certs', 'brz_ca.cer')
    TIMEOUT = 10

    class ParamTypes:
        AllDeliveriesType: type[ZeepBaseType]
        PagingType: type[ZeepBaseType]
        NewDeliveriesOnlyType: type[ZeepBaseType]
    types = ParamTypes()

    def __init__(self, p12_path: str, p12_pass: str|None=None, *, prod: bool=False) -> None:
        self.endpoint = self.PROD_ENDPOINT_URL if prod else self.DEMO_ENDPOINT_URL
        self.soap_endpoint = urljoin(self.endpoint, 'soap')
        self.attachment_endpoint = urljoin(self.endpoint, 'attachment')

        from . import __version__
        self.headers = {'User-Agent': f'USP_MP/{__version__}'}

        private_key, cert = self.load_p12_cert(p12_path, p12_pass)
        self.ssl_context = self.build_ssl_context(private_key, cert)
        del private_key  # only the SSLContext keeps the key from here on

        # Read in types from wsdl. Not strictly needed, but adds some sanity checks
        self.read_wsdl_types()

    def read_wsdl_types(self) -> None:
        """Extract type proxies from wsdl definition file"""

        client = Client(wsdl=self.WSDL_PATH)
        if not client.wsdl.services:
            raise ValueError("No services found in WSDL")

        for type_name in ('AllDeliveriesType', 'PagingType', 'NewDeliveriesOnlyType'):
            t = client.get_type(f'ns0:{type_name}')
            if t is None:
                raise ValueError(f"Could not resolve type {type_name} in WSDL")
            setattr(self.types, type_name, t)

    def load_p12_cert(self, p12_path: str, p12_pass: str|None = None):
        """Load PKCS12 and extract certificate and private key"""

        with open(p12_path, "rb") as f:
            p12_data = f.read()
        password = p12_pass.encode() if p12_pass else None
        private_key, certificate, additional_certs = pkcs12.load_key_and_certificates(
            p12_data, password, default_backend()
        )
        return private_key, certificate

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
            ctx.load_cert_chain(cert_path, key_path, password=one_time_password)

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
            # Note: strict=True throws Exception for GetDelivery()
            # zeep.exceptions.XMLParseError: Unexpected element '{http://reference.e-government.gv.at/namespace/persondata/phase2/20181206#}CorporateBody', expected '{http://reference.e-government.gv.at/namespace/persondata/phase2/20181206#}Person'
            # I don't really care…
            settings = Settings(strict=False, extra_http_headers=uspmp.headers)  # pyright: ignore[reportCallIssue]

            # We can't get the wsdl from the well known endpoint ?wsdl, load wsdl from local file
            self.client = Client(wsdl=uspmp.WSDL_PATH, transport=transport, settings=settings)

            # we already checked that client bindings contains services
            binding = list(self.client.wsdl.bindings.keys())[0]
            self._service = self.client.create_service(binding, uspmp.soap_endpoint)

        def _ensure_open(self) -> None:
            if self.closed:
                raise RuntimeError("Mailbox connection is closed")

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

        def download_attachment(self, delivery_id: str, attachment_id: str) -> bytes:
            self._ensure_open()
            # Must go through the session: the client certificate lives in its SSLContext
            r = self.session.get(self.uspmp.attachment_endpoint,
                params={'delivery_id': delivery_id, 'attachment_id': attachment_id},
                timeout=self.uspmp.TIMEOUT,
            )
            r.raise_for_status()
            return r.content

    def connection(self):
        """Open a new connection; use as a context manager. Mailbox does this for you."""
        return self.ConnectionContext(self)
