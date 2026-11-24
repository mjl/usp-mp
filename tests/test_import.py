# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

"""Import smoke tests. Run on the oldest supported Python (3.11) to catch annotations that only
import on newer versions (e.g. forward references without ``from __future__ import annotations``).
"""

import importlib
import re
import sys

import pytest


@pytest.mark.parametrize("module", ["usp_mp", "usp_mp.adapter", "usp_mp.mail", "usp_mp.cli"])
def test_module_imports(module):
    importlib.import_module(module)


def test_public_api():
    import usp_mp

    assert set(usp_mp.__all__) == {
        "ApiError", "CertificateError", "ChecksumError", "Mailbox", "MailboxStateError", "USP_MP_Adapter", "UspMpError",
    }
    for name in usp_mp.__all__:
        assert getattr(usp_mp, name)
    assert re.fullmatch(r"\d+\.\d+\.\d+", usp_mp.__version__)


def test_cli_help_exits_cleanly(monkeypatch, capsys):
    from usp_mp.cli import main

    monkeypatch.setattr(sys, "argv", ["uspmp", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    assert "--client-certificate" in capsys.readouterr().out
