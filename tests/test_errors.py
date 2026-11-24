# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

"""The exception hierarchy and raise_on_error."""

import pytest

from usp_mp.errors import ApiError, CertificateError, ChecksumError, MailboxStateError, UspMpError
from usp_mp.mail import raise_on_error


@pytest.mark.parametrize(
    ("error", "builtin"),
    [
        (ApiError("1", "m", "Op"), ValueError),
        (ChecksumError("x"), ValueError),
        (MailboxStateError("x"), RuntimeError),
        (CertificateError("x"), None),
    ],
)
def test_hierarchy(error, builtin):
    """All derive from UspMpError; some keep the builtin type older versions raised."""
    assert isinstance(error, UspMpError)
    if builtin is not None:
        assert isinstance(error, builtin)


def test_api_error_attributes_and_message():
    e = ApiError("000", "Unkown uuid.", "GetDelivery")

    assert (e.code, e.message, e.operation) == ("000", "Unkown uuid.", "GetDelivery")
    assert str(e) == "GetDelivery failed: [000] Unkown uuid."


def test_raise_on_error_with_error(wsdl_client):
    response = wsdl_client.get_element("ns0:CloseDeliveryResponse")(
        Error={"ErrorCode": "000", "ErrorMessage": "Unkown uuid."}
    )

    with pytest.raises(ApiError) as excinfo:
        raise_on_error(response, "CloseDelivery")

    assert (excinfo.value.code, excinfo.value.operation) == ("000", "CloseDelivery")


def test_raise_on_error_without_error(wsdl_client):
    response = wsdl_client.get_element("ns0:CloseDeliveryResponse")(Success=True)

    raise_on_error(response, "CloseDelivery")


def test_raise_on_error_tolerates_missing_attribute():
    raise_on_error(object(), "Anything")
