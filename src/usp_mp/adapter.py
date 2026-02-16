
import os
import requests
import tempfile
from urllib.parse import urljoin

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.backends import default_backend

from zeep import Client, Settings
from zeep.transports import Transport
from zeep.xsd.types.base import Type as ZeepBaseType

class USP_MP_Adapter:

    DEMO_ENDPOINT_URL = "https://demo-autoabholung.meinpostkorb.brz.gv.at"
    PROD_ENDPOINT_URL = "https://autoabholung.meinpostkorb.brz.gv.at"

    VERSION = "2.4.0-004"
    WSDL_PATH = os.path.join(os.path.dirname(__file__), 'wsdl', 'zuseaa_p2.wsdl')
    CA_PATH = os.path.join(os.path.dirname(__file__), 'certs', 'brz_ca.cer')

    class param_types:
        AllDeliveriesType: type[ZeepBaseType]
        PagingType: type[ZeepBaseType]
        NewDeliveriesOnlyType: type[ZeepBaseType]
    types = param_types()

    def __init__(self, p12_path: str, p12_pass: str|None=None, prod: bool=False) -> None:
        self.endpoint = self.PROD_ENDPOINT_URL if prod else self.DEMO_ENDPOINT_URL
        self.soap_endpoint = urljoin(self.endpoint, 'soap')
        self.attachment_endpoint = urljoin(self.endpoint, 'attachment')

        from . import __version__
        self.headers = {'User-Agent': f'USP_MP/{__version__}'}

        private_key, cert = self.load_p12_cert(p12_path, p12_pass)
        self.cert_pem, self.key_pem = self.read_cert(private_key, cert)

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

    def read_cert(self, private_key, certificate):
        """Read cert and key pem rom private_key and certificate"""
        cert_pem = certificate.public_bytes(encoding=serialization.Encoding.PEM)
        key_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cert_pem, key_pem

    class ConnectionContext:
        def __init__(self, uspmp):
            self.service = None
            self.uspmp = uspmp
            self.session = requests.Session()
            self.session.verify = uspmp.CA_PATH

            transport = Transport(session=self.session, timeout=10, operation_timeout=10)
            # Note: strict=True throws Exception for GetDelivery()
            # zeep.exceptions.XMLParseError: Unexpected element '{http://reference.e-government.gv.at/namespace/persondata/phase2/20181206#}CorporateBody', expected '{http://reference.e-government.gv.at/namespace/persondata/phase2/20181206#}Person'
            # I don't really care…
            settings = Settings(strict=False, extra_http_headers=uspmp.headers)  # pyright: ignore[reportCallIssue]

            # We can't get the wsdl from the well known endpoint ?wsdl, need to include it in project
            self.client = Client(wsdl=uspmp.WSDL_PATH, transport=transport, settings=settings)

            # we already checked that client bindings contains services
            binding = list(self.client.wsdl.bindings.keys())[0]
            self.service = self.client.create_service(binding, uspmp.soap_endpoint)

        def __enter__(self):
            cert_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            cert_file.write(self.uspmp.cert_pem)
            cert_file.close()

            key_file = tempfile.NamedTemporaryFile(suffix=".pem", delete=False)
            key_file.write(self.uspmp.key_pem)
            key_file.close()

            self.session.cert = (cert_file.name, key_file.name)

            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            self.session.close()
            if self.session.cert:
                for tfile in self.session.cert:
                    try:
                        os.unlink(tfile)
                    except OSError:
                        pass

        def download_attachment(self, delivery_id: str, attachment_id: str) -> bytes:
            r = requests.get(self.uspmp.attachment_endpoint,
                params={'delivery_id': delivery_id, 'attachment_id': attachment_id},
                verify=self.uspmp.CA_PATH,
                cert=self.session.cert
            )
            r.raise_for_status()
            return r.content

    def connection(self):
        """Use as context handler"""
        return self.ConnectionContext(self)
