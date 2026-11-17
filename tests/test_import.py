"""Import smoke tests. Run on the oldest supported Python (3.11) to catch B10-style regressions."""

import importlib
import re
import sys

import pytest


@pytest.mark.parametrize("module", ["usp_mp", "usp_mp.adapter", "usp_mp.mail", "usp_mp.cli"])
def test_module_imports(module):
    importlib.import_module(module)


def test_public_api():
    import usp_mp

    assert usp_mp.__all__ == ["USP_MP_Adapter"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", usp_mp.__version__)


def test_cli_help_exits_cleanly(monkeypatch, capsys):
    from usp_mp.cli import main

    monkeypatch.setattr(sys, "argv", ["uspmp", "--help"])
    with pytest.raises(SystemExit) as excinfo:
        main()

    assert excinfo.value.code == 0
    assert "--client-certificate" in capsys.readouterr().out
