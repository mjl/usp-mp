"""The bundled WSDL/XSD files load and produce the requests the vendor HowTo documents."""

import re
from pathlib import Path

import pytest
from lxml import etree
from zeep import Client, Settings
from zeep.exceptions import XMLParseError

from usp_mp.adapter import USP_MP_Adapter
from usp_mp.mail import unparsed_elements

NS = "http://reference.e-government.gv.at/namespace/zustellung/autoabholung/phase2/20181206#"
SOAP12_ENV = "http://www.w3.org/2003/05/soap-envelope"


def _query_request(wsdl_client, selector):
    service = wsdl_client.create_service(
        next(iter(wsdl_client.wsdl.bindings)), "https://example.invalid/soap"
    )
    envelope = wsdl_client.create_message(service, "QueryDeliveries", selector, Version=USP_MP_Adapter.VERSION)
    return envelope.find(f"{{{SOAP12_ENV}}}Body/{{{NS}}}QueryDeliveriesRequest")


def test_wsdl_has_services(wsdl_client):
    assert wsdl_client.wsdl.services
    assert wsdl_client.wsdl.bindings


@pytest.mark.parametrize("type_name", ["AllDeliveriesType", "PagingType", "NewDeliveriesOnlyType"])
def test_query_types_resolve(wsdl_client, type_name):
    assert wsdl_client.get_type(f"ns0:{type_name}") is not None


@pytest.mark.parametrize(
    "operation", ["QueryDeliveries", "GetDelivery", "CloseDelivery", "DeleteDelivery"]
)
def test_operations_exist(wsdl_client, operation):
    binding = next(iter(wsdl_client.wsdl.bindings.values()))
    assert binding.get(operation) is not None  # raises ValueError if missing


XSI_TYPE = "{http://www.w3.org/2001/XMLSchema-instance}type"

# Note: the vendor HowTo shows the substitution-group elements <aa:AllDeliveries> /
# <aa:NewDeliveriesOnly>. zeep cannot emit those; it sends the head element
# <aa:Query xsi:type="aa:AllDeliveriesType"> instead. That is schema-valid (`Query`
# itself is not abstract, only its type is). These tests pin down what is actually
# sent; tests/test_demo.py confirms that the service accepts it.


def test_all_deliveries_request_shape(wsdl_client):
    types = {n: wsdl_client.get_type(f"ns0:{n}") for n in ("AllDeliveriesType", "PagingType")}
    selector = types["AllDeliveriesType"](Paging=types["PagingType"](Start=0, Limit=100))

    request = _query_request(wsdl_client, selector)

    assert request.get(f"{{{NS}}}Version") == USP_MP_Adapter.VERSION
    query = request.find(f"{{{NS}}}Query")
    assert query.get(XSI_TYPE).endswith(":AllDeliveriesType")
    assert query.findtext(f"{{{NS}}}Paging/{{{NS}}}Start") == "0"
    assert query.findtext(f"{{{NS}}}Paging/{{{NS}}}Limit") == "100"


def test_new_deliveries_request_shape(wsdl_client):
    selector = wsdl_client.get_type("ns0:NewDeliveriesOnlyType")(Limit=100)

    request = _query_request(wsdl_client, selector)

    query = request.find(f"{{{NS}}}Query")
    assert query.get(XSI_TYPE).endswith(":NewDeliveriesOnlyType")
    assert query.findtext(f"{{{NS}}}Limit") == "100"


def test_load_wsdl_resolves_types():
    """USP_MP_Adapter.load_wsdl works without certificate or network."""
    document, types = USP_MP_Adapter.load_wsdl(USP_MP_Adapter.zeep_settings({}))

    assert document.services
    for name in ("AllDeliveriesType", "PagingType", "NewDeliveriesOnlyType"):
        assert getattr(types, name) is not None
    assert document.settings.strict is False


def test_query_types_are_immutable():
    import dataclasses

    _, types = USP_MP_Adapter.load_wsdl()
    with pytest.raises(dataclasses.FrozenInstanceError):
        types.PagingType = None


def test_no_class_level_types():
    """Query types used to live in one class-level object that every adapter wrote into."""
    assert not hasattr(USP_MP_Adapter, "types")


# --- p:Person substitution patch (wsdl/PATCHES.md) ----------------------------------

RECORDED_GET_DELIVERY = Path(__file__).parent / "data" / "get_delivery_response.xml"
WSDL_DIR = Path(USP_MP_Adapter.WSDL_PATH).parent


def parse_get_delivery(xml: bytes, strict: bool):
    client = Client(USP_MP_Adapter.WSDL_PATH, settings=Settings(strict=strict))
    body = etree.fromstring(xml).find(f"{{{SOAP12_ENV}}}Body")[0]
    return client.get_element("ns0:GetDeliveryResponse").parse(body, client.wsdl.types)


@pytest.mark.parametrize("strict", [True, False])
def test_recorded_demo_response_parses_completely(strict):
    """A GetDeliveryResponse recorded from the demo service, with CorporateBody in Sender/Receiver."""
    response = parse_get_delivery(RECORDED_GET_DELIVERY.read_bytes(), strict)
    delivery = response.Delivery

    assert delivery.Sender.CorporateBody.FullName == "Bundeskanzleramt"
    assert delivery.Receiver.CorporateBody.FullName == "Testfirma GmbH"
    assert delivery.Sender.Identification.Type == "urn:publicid:gv.at:baseid+XGLN"
    assert delivery.MetaData.Subject == "Test-Nachricht: 01"
    assert unparsed_elements(response) == []


def test_unknown_element_is_kept_raw_and_detected():
    xml = RECORDED_GET_DELIVERY.read_bytes().replace(
        b"</p:CorporateBody>", b"</p:CorporateBody><p:FutureElement>x</p:FutureElement>", 1
    )

    # zeep stops mapping the rest of the sequence at the unknown element, so the following
    # (known) AdditionalCriteria lands in _raw_elements too
    assert unparsed_elements(parse_get_delivery(xml, strict=False)) == ["FutureElement", "AdditionalCriteria"]
    with pytest.raises(XMLParseError):
        parse_get_delivery(xml, strict=True)


def test_no_unpatched_person_reference_left():
    """Guards a vendor update that forgets to re-apply the patch."""
    for xsd in WSDL_DIR.glob("*.xsd"):
        text = re.sub(r"<!--.*?-->", "", xsd.read_text(encoding="utf-8"), flags=re.S)
        assert 'ref="p:Person"' not in text, xsd.name


def test_person_substitution_group_has_only_the_patched_members():
    """The patch's choice is only equivalent if these are the sole members of the group."""
    members = set()
    for xsd in WSDL_DIR.glob("*.xsd"):
        members |= set(re.findall(r'name="(\w+)"[^>]*substitutionGroup="p:Person"', xsd.read_text(encoding="utf-8")))

    assert members == {"PhysicalPerson", "CorporateBody"}

