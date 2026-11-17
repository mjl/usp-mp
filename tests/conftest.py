"""Shared fixtures. The fakes themselves live in fakes.py.

Demo-service tests (marker ``demo``, tests/test_demo.py) need a client
certificate. Without one they are skipped. Configure it with options or
environment variables (options win):

    --p12 PATH                    USP_MP_P12
    --p12-password-file PATH      USP_MP_P12_PASS_FILE
    --p12-password VALUE          USP_MP_P12_PASS   (may be empty: no password)
"""

import os
from dataclasses import dataclass

import pytest
import zeep

from fakes import FakeAdapter, FakeService
from usp_mp.adapter import USP_MP_Adapter


# --- demo-service configuration -----------------------------------------------------

def pytest_addoption(parser):
    group = parser.getgroup("usp-mp", "Mein Postkorb demo-service tests")
    group.addoption("--p12", metavar="PATH", help="client certificate (.p12) for the demo service [env: USP_MP_P12]")
    group.addoption("--p12-password-file", metavar="PATH",
                    help="file whose first line is the .p12 password [env: USP_MP_P12_PASS_FILE]")
    group.addoption("--p12-password", metavar="VALUE",
                    help="the .p12 password; visible in the process list, prefer the file [env: USP_MP_P12_PASS]")
    group.addoption("--update-demo-snapshot", action="store_true",
                    help="rewrite tests/data/demo_snapshot.json from the live demo data instead of comparing")


@dataclass(frozen=True)
class DemoCredentials:
    p12_path: str
    p12_pass: str = ""

    def __repr__(self) -> str:  # never show the password in test output
        return f"DemoCredentials(p12_path={self.p12_path!r}, p12_pass=***)"


def _demo_credentials(config) -> DemoCredentials | None:
    p12 = config.getoption("--p12") or os.environ.get("USP_MP_P12")
    if not p12:
        return None
    if not os.path.isfile(p12):
        raise pytest.UsageError(f"demo tests: certificate {p12} does not exist")

    pw_file = config.getoption("--p12-password-file") or os.environ.get("USP_MP_P12_PASS_FILE")
    pw = config.getoption("--p12-password")
    if pw is None and not pw_file:
        pw = os.environ.get("USP_MP_P12_PASS")
    if pw is None and pw_file:
        try:
            with open(pw_file, encoding="utf-8") as f:
                pw = f.readline().rstrip("\r\n")
        except OSError as e:
            raise pytest.UsageError(f"demo tests: cannot read password file: {e}") from None
    if pw is None:
        raise pytest.UsageError(
            "demo tests: a certificate is configured but no password; use --p12-password-file, "
            "USP_MP_P12_PASS_FILE or USP_MP_P12_PASS (empty for none)"
        )
    return DemoCredentials(p12, pw)


def pytest_configure(config):
    config.usp_mp_demo_credentials = _demo_credentials(config)


def pytest_collection_modifyitems(config, items):
    if config.usp_mp_demo_credentials is not None:
        return
    skip = pytest.mark.skip(reason="demo-service tests need a client certificate: --p12 / USP_MP_P12")
    for item in items:
        if "demo" in item.keywords:
            item.add_marker(skip)


def pytest_report_header(config):
    creds = config.usp_mp_demo_credentials
    return f"usp-mp demo service: {'certificate ' + creds.p12_path if creds else 'no certificate, demo tests skipped'}"


@pytest.fixture(scope="session")
def demo_credentials(pytestconfig) -> DemoCredentials:
    creds = pytestconfig.usp_mp_demo_credentials
    if creds is None:  # only reachable if a test requests it without the demo marker
        pytest.skip("no demo-service certificate configured")
    return creds


# --- fakes --------------------------------------------------------------------------


@pytest.fixture(scope="session")
def wsdl_client():
    """zeep client for the bundled WSDL (parsed once per test session)."""
    return zeep.Client(USP_MP_Adapter.WSDL_PATH)


@pytest.fixture
def service(wsdl_client):
    """An empty fake mailbox server; add deliveries with ``service.add_delivery()``."""
    return FakeService(wsdl_client)


@pytest.fixture
def fake_adapters(service, wsdl_client, monkeypatch):
    """Patch ``usp_mp.mail.USP_MP_Adapter`` with the fake; yields the adapters created."""
    from usp_mp import mail

    created = []

    def factory(p12_path, p12_pass=None, *, prod=False):
        adapter = FakeAdapter(service, wsdl_client, p12_path, p12_pass, prod=prod)
        created.append(adapter)
        return adapter

    monkeypatch.setattr(mail, "USP_MP_Adapter", factory)
    return created


@pytest.fixture
def mailbox(fake_adapters):
    """An open ``Mailbox`` wired to the fake service: no certificate, no network."""
    from usp_mp.mail import Mailbox

    with Mailbox("fake.p12", "secret") as mbox:
        yield mbox
