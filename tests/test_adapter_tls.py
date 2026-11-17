"""Client-certificate (mutual TLS) handling of USP_MP_Adapter, against a local HTTPS server."""

import datetime
import http.server
import json
import os
import ssl
import threading
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape as xml_escape

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID

from usp_mp.adapter import USP_MP_Adapter
from usp_mp.errors import CertificateError, MailboxStateError

P12_PASSWORD = "p12-secret"


# --- certificate helpers -------------------------------------------------------

def _make_cert(cn, issuer=None, *, ca=False, san=None):
    """Return (key, cert); self-signed when issuer is None, else issuer=(key, cert)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer_key, issuer_cert = issuer if issuer else (key, None)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer_cert.subject if issuer_cert else name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        # SKI/AKI are required by VERIFY_X509_STRICT (default on Python 3.13+)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), critical=False
        )
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
    if san:
        builder = builder.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
    return key, builder.sign(issuer_key, hashes.SHA256())


def _pem(cert):
    return cert.public_bytes(serialization.Encoding.PEM)


def _key_pem(key):
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("pki")
    ca = _make_cert("test-ca", ca=True)
    other_ca = _make_cert("other-ca", ca=True)
    server = _make_cert("localhost", ca, san="localhost")
    untrusted_server = _make_cert("localhost", other_ca, san="localhost")
    client = _make_cert("test-client", ca)

    paths = {}
    for name, data in {
        "ca": _pem(ca[1]),
        "server_cert": _pem(server[1]),
        "server_key": _key_pem(server[0]),
        "untrusted_cert": _pem(untrusted_server[1]),
        "untrusted_key": _key_pem(untrusted_server[0]),
    }.items():
        p = d / f"{name}.pem"
        p.write_bytes(data)
        paths[name] = str(p)

    p12 = d / "client.p12"
    p12.write_bytes(
        pkcs12.serialize_key_and_certificates(
            b"test-client", client[0], client[1], None,
            serialization.BestAvailableEncryption(P12_PASSWORD.encode()),
        )
    )
    paths["p12"] = str(p12)
    return paths


# --- local mTLS server ---------------------------------------------------------

class _EchoHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        peer = self.connection.getpeercert()
        subject = dict(rdn[0] for rdn in peer["subject"]) if peer else {}
        body = json.dumps({
            "client_cn": subject.get("commonName"),
            "path": urlsplit(self.path).path,
            "query": parse_qs(urlsplit(self.path).query),
            "user_agent": self.headers.get("User-Agent"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        # SOAP 1.2 calls: report who connected inside a Fault, which zeep raises
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        peer = self.connection.getpeercert()
        subject = dict(rdn[0] for rdn in peer["subject"]) if peer else {}
        info = xml_escape(json.dumps({
            "client_cn": subject.get("commonName"),
            "user_agent": self.headers.get("User-Agent"),
        }))
        body = (
            '<env:Envelope xmlns:env="http://www.w3.org/2003/05/soap-envelope"><env:Body>'
            '<env:Fault><env:Code><env:Value>env:Receiver</env:Value></env:Code>'
            f'<env:Reason><env:Text xml:lang="en">{info}</env:Text></env:Reason>'
            '</env:Fault></env:Body></env:Envelope>'
        ).encode()
        self.send_response(500)
        self.send_header("Content-Type", "application/soap+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def start_server(pki):
    servers = []

    def start(cert="server_cert", key="server_key"):
        httpd = http.server.ThreadingHTTPServer(("localhost", 0), _EchoHandler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(pki[cert], pki[key])
        ctx.verify_mode = ssl.CERT_REQUIRED  # demand a client certificate
        ctx.load_verify_locations(pki["ca"])
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return httpd.server_port

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


@pytest.fixture(autouse=True)
def _no_proxies(monkeypatch):
    for var in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def adapter(pki, monkeypatch):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    return USP_MP_Adapter(pki["p12"], P12_PASSWORD)


@pytest.fixture
def record_key_loading(monkeypatch):
    """Wrap SSLContext.load_cert_chain to capture what is on disk at load time."""
    calls = []
    original = ssl.SSLContext.load_cert_chain

    def wrapper(self, certfile, keyfile=None, password=None):
        calls.append({
            "dir": os.path.dirname(keyfile),
            "dir_mode": os.stat(os.path.dirname(keyfile)).st_mode & 0o777,
            "key_mode": os.stat(keyfile).st_mode & 0o777,
            "key_pem": open(keyfile, "rb").read(),
            "password": password,
        })
        return original(self, certfile, keyfile, password)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", wrapper)
    return calls


# --- tests ---------------------------------------------------------------------

def test_attachment_download_presents_client_certificate(adapter, start_server):
    port = start_server()
    adapter.attachment_endpoint = f"https://localhost:{port}/attachment"

    with adapter.connection() as conn:
        data = json.loads(conn.download_attachment("d-1", "a-2"))

    assert data["client_cn"] == "test-client"
    assert data["path"] == "/attachment"
    assert data["query"] == {"delivery_id": ["d-1"], "attachment_id": ["a-2"]}
    assert data["user_agent"].startswith("USP_MP/")


def test_soap_call_presents_client_certificate(adapter, start_server):
    from zeep.exceptions import Fault

    port = start_server()
    adapter.soap_endpoint = f"https://localhost:{port}/soap"

    with adapter.connection() as conn, pytest.raises(Fault) as excinfo:
        conn.service.GetDelivery("d-1", Version=adapter.VERSION)

    data = json.loads(excinfo.value.message)
    assert data["client_cn"] == "test-client"
    assert data["user_agent"].startswith("USP_MP/")


def test_wsdl_parsed_once_per_adapter(adapter, monkeypatch):
    """Connections reuse the adapter's parsed WSDL instead of parsing it again."""
    import zeep.wsdl.wsdl as zeep_wsdl

    parses = []
    original_init = zeep_wsdl.Document.__init__
    monkeypatch.setattr(zeep_wsdl.Document, "__init__",
                        lambda self, *a, **kw: parses.append(a[0]) or original_init(self, *a, **kw))

    with adapter.connection() as first, adapter.connection() as second:
        assert first.client.wsdl is adapter.wsdl
        assert second.client.wsdl is adapter.wsdl
        assert first.client.transport is not second.client.transport  # own session each

    assert parses == []


def test_adapters_have_independent_types(pki, monkeypatch):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    a = USP_MP_Adapter(pki["p12"], P12_PASSWORD)
    b = USP_MP_Adapter(pki["p12"], P12_PASSWORD)

    assert a.types is not b.types
    assert a.wsdl is not b.wsdl


def test_connection_unusable_after_close(adapter, start_server):
    port = start_server()
    adapter.attachment_endpoint = f"https://localhost:{port}/attachment"

    with adapter.connection() as conn:
        conn.download_attachment("d", "a")

    assert conn.closed
    with pytest.raises(MailboxStateError, match="closed"):
        conn.service
    with pytest.raises(MailboxStateError, match="closed"):
        conn.download_attachment("d", "a")
    with pytest.raises(MailboxStateError, match="closed"):
        conn.__enter__()
    conn.close()  # idempotent


def test_mailbox_end_to_end_over_mutual_tls(pki, monkeypatch, start_server):
    """Real Mailbox + adapter (no fakes): the SOAP call carries the client certificate."""
    from zeep.exceptions import Fault

    from usp_mp.mail import Mailbox

    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    port = start_server()
    mbox = Mailbox(pki["p12"], P12_PASSWORD)
    mbox.uspmp.soap_endpoint = f"https://localhost:{port}/soap"

    with mbox, pytest.raises(Fault) as excinfo:
        list(mbox.deliveries())

    assert json.loads(excinfo.value.message)["client_cn"] == "test-client"
    with pytest.raises(MailboxStateError, match="closed"):
        mbox.deliveries()


def test_private_key_only_written_encrypted_and_removed(pki, monkeypatch, record_key_loading):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    USP_MP_Adapter(pki["p12"], P12_PASSWORD)

    [call] = record_key_loading
    assert b"-----BEGIN ENCRYPTED PRIVATE KEY-----" in call["key_pem"]
    assert b"-----BEGIN PRIVATE KEY-----" not in call["key_pem"]
    assert call["password"] and call["password"] != P12_PASSWORD.encode()
    assert call["dir_mode"] == 0o700
    assert call["key_mode"] == 0o600
    assert not os.path.exists(call["dir"])


def test_one_time_password_differs_per_adapter(pki, monkeypatch, record_key_loading):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    USP_MP_Adapter(pki["p12"], P12_PASSWORD)
    USP_MP_Adapter(pki["p12"], P12_PASSWORD)

    assert record_key_loading[0]["password"] != record_key_loading[1]["password"]


def test_temp_dir_removed_when_loading_fails(pki, monkeypatch, record_key_loading):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    recorded_wrapper = ssl.SSLContext.load_cert_chain

    def failing(self, certfile, keyfile=None, password=None):
        recorded_wrapper(self, certfile, keyfile, password)
        raise ssl.SSLError("simulated failure")

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", failing)

    with pytest.raises(CertificateError, match="simulated failure") as excinfo:
        USP_MP_Adapter(pki["p12"], P12_PASSWORD)

    assert isinstance(excinfo.value.__cause__, ssl.SSLError)

    assert record_key_loading
    assert not os.path.exists(record_key_loading[0]["dir"])


def test_adapter_keeps_no_plaintext_key(adapter):
    assert not hasattr(adapter, "key_pem")
    assert not hasattr(adapter, "cert_pem")


def test_server_outside_pinned_ca_is_rejected(adapter, start_server):
    port = start_server(cert="untrusted_cert", key="untrusted_key")
    adapter.attachment_endpoint = f"https://localhost:{port}/attachment"

    with adapter.connection() as conn, pytest.raises(requests.exceptions.SSLError):
        conn.download_attachment("d", "a")


def test_hostname_mismatch_is_rejected(adapter, start_server):
    port = start_server()  # certificate is for "localhost" only
    adapter.attachment_endpoint = f"https://127.0.0.1:{port}/attachment"

    with adapter.connection() as conn, pytest.raises(requests.exceptions.SSLError):
        conn.download_attachment("d", "a")


def test_debug_logs_contain_no_secrets(pki, monkeypatch, caplog, start_server):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    port = start_server()

    with caplog.at_level("DEBUG"):
        adapter = USP_MP_Adapter(pki["p12"], P12_PASSWORD)
        adapter.attachment_endpoint = f"https://localhost:{port}/attachment"
        with adapter.connection() as conn:
            content = conn.download_attachment("d-1", "a-2")

    assert "Using demo endpoint" in caplog.text
    assert "Downloaded attachment a-2 of delivery d-1" in caplog.text
    assert P12_PASSWORD not in caplog.text
    assert "PRIVATE KEY" not in caplog.text and "BEGIN" not in caplog.text
    assert content.decode() not in caplog.text  # attachment contents are never logged


@pytest.mark.parametrize(("days_left", "level", "message"), [
    (400, "DEBUG", "Client certificate valid until"),
    (10, "WARNING", "expires on"),
    (-1, "WARNING", "expired on"),
])
def test_certificate_expiry_check(days_left, level, message, caplog):
    import datetime
    from types import SimpleNamespace

    expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days_left)
    with caplog.at_level("DEBUG", logger="usp_mp.adapter"):
        result = USP_MP_Adapter.check_expiry(SimpleNamespace(not_valid_after_utc=expires))

    assert result == expires
    [record] = [r for r in caplog.records if r.name == "usp_mp.adapter"]
    assert (record.levelname, message in record.getMessage()) == (level, True)


def test_adapter_records_certificate_expiry(adapter):
    import datetime

    assert adapter.certificate_expires > datetime.datetime.now(datetime.timezone.utc)


def test_wrong_p12_password_fails(pki, monkeypatch):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    with pytest.raises(CertificateError, match="wrong password or not a PKCS#12 file"):
        USP_MP_Adapter(pki["p12"], "wrong")


def test_missing_p12_fails(tmp_path):
    with pytest.raises(CertificateError, match="Cannot read client certificate"):
        USP_MP_Adapter(str(tmp_path / "missing.p12"), "pw")


def test_p12_without_private_key_fails(pki, tmp_path, monkeypatch):
    monkeypatch.setattr(USP_MP_Adapter, "CA_PATH", pki["ca"])
    cert = x509.load_pem_x509_certificate(open(pki["ca"], "rb").read())
    p12 = tmp_path / "cert-only.p12"
    p12.write_bytes(pkcs12.serialize_key_and_certificates(
        b"no-key", None, cert, None, serialization.BestAvailableEncryption(b"pw")))

    with pytest.raises(CertificateError, match="contains no private key"):
        USP_MP_Adapter(str(p12), "pw")
