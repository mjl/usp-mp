"""The bundled WSDL/XSD files load and produce the requests the vendor HowTo documents."""

import pytest

from usp_mp.adapter import USP_MP_Adapter

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


def test_adapter_reads_types_from_wsdl():
    """USP_MP_Adapter.read_wsdl_types works without certificate or network."""
    adapter = USP_MP_Adapter.__new__(USP_MP_Adapter)  # skip __init__: no .p12 needed
    adapter.read_wsdl_types()

    for name in ("AllDeliveriesType", "PagingType", "NewDeliveriesOnlyType"):
        assert getattr(adapter.types, name) is not None
