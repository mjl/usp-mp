# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

"""The bundled BRZ CA certificate (the trust anchor for the service's TLS certificate).

Downloaded from the link on the USP Mein Postkorb page where client
certificates are generated ("Automatische Abholung" -> "Zertifikat erzeugen").
docs/howto.md, "Trust anchor", documents these values. If the certificate is
ever replaced, update both together.
"""

import datetime
import warnings

from cryptography import x509
from cryptography.hazmat.primitives import hashes

from usp_mp.adapter import USP_MP_Adapter

SHA256_FINGERPRINT = "381092B4EB0839257374E4105B662536204D9881944AE7DB49B08E168A8F2EA9"
SUBJECT = "CN=BRZ-StammCA-2-02,OU=BRZ CA,O=Bundesrechenzentrum GmbH,C=AT"
EXPIRY_WARNING = datetime.timedelta(days=365)


def load_ca() -> x509.Certificate:
    with open(USP_MP_Adapter.CA_PATH, "rb") as f:
        return x509.load_pem_x509_certificate(f.read())


def test_bundled_ca_matches_documented_fingerprint():
    assert load_ca().fingerprint(hashes.SHA256()).hex().upper() == SHA256_FINGERPRINT


def test_bundled_ca_is_a_self_signed_root():
    ca = load_ca()

    assert ca.subject.rfc4514_string() == SUBJECT
    assert ca.issuer == ca.subject
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca


def test_bundled_ca_is_valid_and_warns_before_expiry():
    """Fails only once it has actually expired; warns a year ahead, so this is no time bomb."""
    expires = load_ca().not_valid_after_utc
    remaining = expires - datetime.datetime.now(datetime.timezone.utc)

    assert remaining > datetime.timedelta(0), f"bundled BRZ CA expired on {expires.date()}; TLS to the service will fail"
    if remaining < EXPIRY_WARNING:
        warnings.warn(f"bundled BRZ CA expires on {expires.date()}; obtain its successor (docs/howto.md)", UserWarning)
