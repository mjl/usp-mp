"""Shared fixtures. The fakes themselves live in fakes.py."""

import pytest
import zeep

from fakes import FakeAdapter, FakeService
from usp_mp.adapter import USP_MP_Adapter


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
