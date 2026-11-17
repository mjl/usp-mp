"""Command line behaviour of `uspmp`, without certificate or network."""

import sys

import pytest

from usp_mp import cli


def run_cli(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["uspmp", *args])
    return cli.main()


def test_lists_deliveries(monkeypatch, capsys, fake_adapters, service):
    service.add_delivery("Bescheid 2026")

    assert run_cli(monkeypatch, "--client-certificate", "x.p12", "--client-password", "") == 0

    out = capsys.readouterr().out
    assert "Bescheid 2026" in out
    assert "Mail body 1" in out  # short text/plain body is printed
    assert service.calls_to("CloseDelivery") == []  # nothing marked read without --save


def test_save_writes_attachments_and_marks_read(monkeypatch, tmp_path, fake_adapters, service):
    fake = service.add_delivery()

    run_cli(monkeypatch, "--client-certificate", "x.p12", "--client-password", "",
            "--save", "--download", str(tmp_path))

    assert (tmp_path / "0" / "0_mailbody.txt").read_bytes() == b"Mail body 1\n"
    assert fake.read


def test_password_from_environment(monkeypatch, fake_adapters, service):
    monkeypatch.setenv("TEST_P12_PASS", "from-env")

    run_cli(monkeypatch, "--client-certificate", "x.p12", "--client-password", "ENV:TEST_P12_PASS")

    assert fake_adapters[0].p12_pass == "from-env"


@pytest.mark.xfail(strict=True, reason="critique B7: missing --client-certificate is not an argparse error")
def test_missing_certificate_is_usage_error(monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        run_cli(monkeypatch, "--client-password", "")
    assert excinfo.value.code == 2


@pytest.mark.xfail(strict=True, reason="critique B8: a second --save run into the same directory crashes")
def test_save_twice_into_same_directory(monkeypatch, tmp_path, fake_adapters, service):
    service.add_delivery()
    args = ("--client-certificate", "x.p12", "--client-password", "", "--save", "--download", str(tmp_path))

    run_cli(monkeypatch, *args)
    run_cli(monkeypatch, *args, "--all")
