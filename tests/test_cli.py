# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

"""Command line behaviour of `uspmp`, without certificate or network."""

import getpass

import pytest

from usp_mp import cli
from usp_mp.errors import ApiError


@pytest.fixture
def cert(tmp_path):
    """An existing (dummy) certificate path; the fake adapter never reads it."""
    path = tmp_path / "x.p12"
    path.write_bytes(b"")
    return str(path)


def run_cli(*args):
    return cli.main(list(args))


def usage_error(capsys, *args):
    """Run the CLI expecting an argparse usage error; return stderr."""
    with pytest.raises(SystemExit) as excinfo:
        run_cli(*args)
    assert excinfo.value.code == 2
    return capsys.readouterr().err


# --- normal operation ---------------------------------------------------------------

def test_lists_deliveries(capsys, cert, fake_adapters, service):
    service.add_delivery("Bescheid 2026")

    assert run_cli("--client-certificate", cert, "--client-password", "") == 0

    out = capsys.readouterr().out
    assert "Bescheid 2026]> from Bundeskanzleramt" in out
    assert "Mail body 1" in out  # short text/plain body is printed
    assert service.calls_to("CloseDelivery") == []  # nothing marked read without --save
    assert service.open_connections == 0


def test_save_writes_attachments_and_marks_read(tmp_path, cert, fake_adapters, service):
    fake = service.add_delivery()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    assert run_cli("--client-certificate", cert, "--client-password", "",
                   "--save", "--download", str(out_dir)) == 0

    assert (out_dir / fake.delivery_id / "0_mailbody.txt").read_bytes() == b"Mail body 1\n"
    assert fake.read
    assert not list(out_dir.rglob("*.part"))


def test_non_utf8_mail_body_is_printed(capsys, cert, fake_adapters, service):
    from fakes import FakeAttachment

    service.add_delivery(attachments=[FakeAttachment(b"Gr\xfc\xdfe\n", "mailbody", "text/plain")])

    assert run_cli("--client-certificate", cert, "--client-password", "") == 0
    assert "Gr" in capsys.readouterr().out


# --- password sources ---------------------------------------------------------------

def test_password_from_environment(monkeypatch, capsys, cert, fake_adapters, service):
    monkeypatch.setenv("TEST_P12_PASS", "from-env")

    run_cli("--client-certificate", cert, "--client-password", "ENV:TEST_P12_PASS")

    assert fake_adapters[0].p12_pass == "from-env"
    assert "warning" not in capsys.readouterr().err


def test_password_from_empty_environment_variable(monkeypatch, cert, fake_adapters, service):
    monkeypatch.setenv("TEST_P12_PASS", "")

    run_cli("--client-certificate", cert, "--client-password", "ENV:TEST_P12_PASS")

    assert fake_adapters[0].p12_pass == ""


def test_unset_environment_variable_is_usage_error(monkeypatch, capsys, cert):
    monkeypatch.delenv("TEST_P12_PASS", raising=False)

    err = usage_error(capsys, "--client-certificate", cert, "--client-password", "ENV:TEST_P12_PASS")

    assert "TEST_P12_PASS" in err and "not set" in err


def test_password_from_file(tmp_path, capsys, cert, fake_adapters, service):
    pw_file = tmp_path / "pw"
    pw_file.write_text("from-file\r\nignored second line\n")

    run_cli("--client-certificate", cert, "--client-password-file", str(pw_file))

    assert fake_adapters[0].p12_pass == "from-file"
    assert "warning" not in capsys.readouterr().err


def test_missing_password_file_is_usage_error(tmp_path, capsys, cert):
    err = usage_error(capsys, "--client-certificate", cert, "--client-password-file", str(tmp_path / "nope"))

    assert "cannot read password file" in err


def test_password_options_are_exclusive(tmp_path, capsys, cert):
    err = usage_error(capsys, "--client-certificate", cert,
                      "--client-password", "", "--client-password-file", str(tmp_path / "pw"))

    assert "not allowed with" in err


def test_literal_password_warns(capsys, cert, fake_adapters, service):
    run_cli("--client-certificate", cert, "--client-password", "secret")

    assert fake_adapters[0].p12_pass == "secret"
    assert "visible to other users" in capsys.readouterr().err


def test_empty_literal_password_does_not_warn(capsys, cert, fake_adapters, service):
    run_cli("--client-certificate", cert, "--client-password", "")

    assert "warning" not in capsys.readouterr().err


def test_prompts_without_password_option(monkeypatch, cert, fake_adapters, service):
    prompts = []
    monkeypatch.setattr(getpass, "getpass", lambda prompt: prompts.append(prompt) or "typed")

    run_cli("--client-certificate", cert)

    assert prompts == [f"Enter {cert} password: "]
    assert fake_adapters[0].p12_pass == "typed"


# --- argument validation ------------------------------------------------------------

def test_missing_certificate_is_usage_error(capsys):
    err = usage_error(capsys, "--client-password", "")

    assert "--client-certificate" in err


def test_nonexistent_certificate_is_usage_error(tmp_path, capsys):
    err = usage_error(capsys, "--client-certificate", str(tmp_path / "missing.p12"), "--client-password", "")

    assert "does not exist" in err


def test_bad_download_directory_is_usage_error(tmp_path, capsys, cert):
    err = usage_error(capsys, "--client-certificate", cert, "--client-password", "",
                      "--download", str(tmp_path / "missing"))

    assert "is not a directory" in err


# --- runtime errors -----------------------------------------------------------------

def test_runtime_error_is_one_line_and_exit_1(capsys, cert, fake_adapters, service):
    service.fail_next("QueryDeliveries", "123", "Mailbox locked")

    assert run_cli("--client-certificate", cert, "--client-password", "") == 1

    err = capsys.readouterr().err
    assert err == "uspmp: error: QueryDeliveries failed: [123] Mailbox locked\n"
    assert "Traceback" not in err
    assert service.open_connections == 0


def test_debug_shows_full_exception(cert, fake_adapters, service, monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda **kwargs: None)
    service.fail_next("QueryDeliveries", "123", "Mailbox locked")

    with pytest.raises(ApiError, match="Mailbox locked"):
        run_cli("--client-certificate", cert, "--client-password", "", "--debug")


def test_certificate_load_failure_is_reported(capsys, cert):
    """Real adapter, empty dummy .p12: fails cleanly instead of with a traceback."""
    assert run_cli("--client-certificate", cert, "--client-password", "") == 1

    assert capsys.readouterr().err == (
        f"uspmp: error: Cannot load client certificate {cert}: wrong password or not a PKCS#12 file\n"
    )


def test_unexpected_error_names_its_type(capsys, cert, fake_adapters, service, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service, "QueryDeliveries", boom)

    assert run_cli("--client-certificate", cert, "--client-password", "") == 1
    assert capsys.readouterr().err == "uspmp: error: OSError: disk full\n"


def test_sys_argv_is_used_by_default(monkeypatch, cert, fake_adapters, service):
    monkeypatch.setattr("sys.argv", ["uspmp", "--client-certificate", cert, "--client-password", ""])

    assert cli.main() == 0


def test_save_twice_into_same_directory(tmp_path, cert, fake_adapters, service):
    """Critique B8: the second run used to fail on the existing directory 0/."""
    fake = service.add_delivery()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    args = ("--client-certificate", cert, "--client-password", "", "--save", "--download", str(out_dir))

    assert run_cli(*args) == 0
    assert run_cli(*args, "--all") == 0

    assert [p.name for p in out_dir.iterdir()] == [fake.delivery_id]
    assert (out_dir / fake.delivery_id / "0_mailbody.txt").read_bytes() == b"Mail body 1\n"


def test_save_directories_are_per_delivery_id(tmp_path, cert, fake_adapters, service):
    """A later run with a different set of deliveries never mixes them up."""
    first = service.add_delivery()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    args = ("--client-certificate", cert, "--client-password", "", "--save", "--download", str(out_dir))

    run_cli(*args)          # saves and closes `first`
    second = service.add_delivery()
    run_cli(*args)          # new-only: just `second`, which was index 0 again

    assert sorted(p.name for p in out_dir.iterdir()) == sorted([first.delivery_id, second.delivery_id])
    assert (out_dir / second.delivery_id / "0_mailbody.txt").read_bytes() == b"Mail body 2\n"
    assert (out_dir / first.delivery_id / "0_mailbody.txt").read_bytes() == b"Mail body 1\n"


# --- logging ------------------------------------------------------------------------

def test_library_warnings_are_shown(capsys, cert, fake_adapters, service, monkeypatch):
    from usp_mp import mail

    monkeypatch.setattr(mail, "PAGE_SIZE", 2)
    service.add_deliveries(3)

    assert run_cli("--client-certificate", cert, "--client-password", "") == 0

    err = capsys.readouterr().err
    assert "uspmp: warning: 3 new deliveries, but only 2 returned" in err
    assert "debug" not in err.lower()


def test_debug_shows_library_debug_output(capsys, cert, fake_adapters, service):
    import logging

    service.add_delivery()

    assert run_cli("--client-certificate", cert, "--client-password", "", "--debug") == 0

    err = capsys.readouterr().err
    assert "usp_mp.mail: DEBUG: QueryDeliveries NewDeliveriesOnlyType Limit=1000: HitCount 1, 1 IDs returned" in err
    assert "usp_mp.mail: DEBUG: Fetched delivery" in err
    assert not logging.getLogger("usp_mp.mail").disabled  # the old dictConfig setup disabled it


def test_setup_logging_is_idempotent():
    import logging

    cli.setup_logging()
    cli.setup_logging(debug=True)

    assert len(logging.getLogger("usp_mp").handlers) == 1
    assert logging.getLogger("usp_mp").level == logging.DEBUG


# --- --delete -------------------------------------------------------------------------

def save_delete_args(cert, out_dir, *extra):
    return ("--client-certificate", cert, "--client-password", "", "--save", "--delete",
            "--download", str(out_dir), *extra)


@pytest.fixture
def out_dir(tmp_path):
    d = tmp_path / "out"
    d.mkdir()
    return d


def test_delete_requires_save(capsys, cert):
    err = usage_error(capsys, "--client-certificate", cert, "--client-password", "", "--delete")

    assert "--delete requires --save" in err


def test_delete_after_successful_save(capsys, cert, out_dir, fake_adapters, service):
    from fakes import FakeAttachment

    first = service.add_delivery()
    second = service.add_delivery(attachments=[
        FakeAttachment(b"body\n", "mailbody", "text/plain"), FakeAttachment(b"%PDF-1.7", "Bescheid.pdf"),
    ])

    assert run_cli(*save_delete_args(cert, out_dir)) == 0

    assert service.deliveries == {}
    assert (out_dir / first.delivery_id / "0_mailbody.txt").read_bytes() == b"Mail body 1\n"
    assert (out_dir / second.delivery_id / "1_bescheid.pdf").read_bytes() == b"%PDF-1.7"
    # the service requires CloseDelivery before DeleteDelivery
    ops = [c[0] for c in service.calls if c[0] in ("CloseDelivery", "DeleteDelivery")]
    assert ops == ["CloseDelivery", "DeleteDelivery"] * 2
    assert capsys.readouterr().out.count("Deleted from the mailbox") == 2


def test_checksum_failure_keeps_delivery(capsys, cert, out_dir, fake_adapters, service):
    from fakes import FakeAttachment

    fake = service.add_delivery(attachments=[
        FakeAttachment(b"body\n", "mailbody", "text/plain"),
        FakeAttachment(b"%PDF-1.7", "Bescheid.pdf", checksum=b"\0" * 32),  # corrupted download
    ])

    assert run_cli(*save_delete_args(cert, out_dir)) == 1

    assert fake.delivery_id in service.deliveries and not fake.read
    assert service.calls_to("CloseDelivery") == service.calls_to("DeleteDelivery") == []
    assert "do not match checksum" in capsys.readouterr().err


def test_write_failure_keeps_delivery(capsys, cert, out_dir, fake_adapters, service, monkeypatch):
    fake = service.add_delivery()

    def disk_full(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(cli, "write_atomic", disk_full)

    assert run_cli(*save_delete_args(cert, out_dir)) == 1

    assert fake.delivery_id in service.deliveries and not fake.read
    assert "No space left on device" in capsys.readouterr().err


def test_verification_failure_keeps_delivery(capsys, cert, out_dir, fake_adapters, service, monkeypatch):
    """A file that doesn't read back as the downloaded bytes blocks deletion."""
    from usp_mp.util import write_atomic

    fake = service.add_delivery()
    monkeypatch.setattr(cli, "write_atomic", lambda path, data: write_atomic(path, data[:-1]))  # silently truncated

    assert run_cli(*save_delete_args(cert, out_dir)) == 1

    assert fake.delivery_id in service.deliveries and not fake.read
    assert "doesn't contain the downloaded data; delivery kept" in capsys.readouterr().err


def test_failure_on_later_delivery_keeps_earlier_deletions(cert, out_dir, fake_adapters, service):
    from fakes import FakeAttachment

    good = service.add_delivery()
    bad = service.add_delivery(attachments=[FakeAttachment(b"x", "a.pdf", checksum=b"\0" * 32)])

    assert run_cli(*save_delete_args(cert, out_dir)) == 1

    assert good.delivery_id not in service.deliveries  # fully saved first, so deleted
    assert bad.delivery_id in service.deliveries and not bad.read


def test_delete_api_error_is_reported(capsys, cert, out_dir, fake_adapters, service):
    fake = service.add_delivery()
    service.fail_next("DeleteDelivery", "000", "Unkown uuid.")

    assert run_cli(*save_delete_args(cert, out_dir)) == 1

    assert (out_dir / fake.delivery_id / "0_mailbody.txt").exists()
    assert "DeleteDelivery failed: [000]" in capsys.readouterr().err


def test_save_without_delete_only_marks_read(cert, out_dir, fake_adapters, service):
    fake = service.add_delivery()

    assert run_cli("--client-certificate", cert, "--client-password", "", "--save", "--download", str(out_dir)) == 0

    assert fake.read and fake.delivery_id in service.deliveries
    assert service.calls_to("DeleteDelivery") == []

