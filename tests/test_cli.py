"""Command line behaviour of `uspmp`, without certificate or network."""

import getpass

import pytest

from usp_mp import cli


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
    assert "Bescheid 2026" in out
    assert "Mail body 1" in out  # short text/plain body is printed
    assert service.calls_to("CloseDelivery") == []  # nothing marked read without --save
    assert service.open_connections == 0


def test_save_writes_attachments_and_marks_read(tmp_path, cert, fake_adapters, service):
    fake = service.add_delivery()
    out_dir = tmp_path / "out"
    out_dir.mkdir()

    assert run_cli("--client-certificate", cert, "--client-password", "",
                   "--save", "--download", str(out_dir)) == 0

    assert (out_dir / "0" / "0_mailbody.txt").read_bytes() == b"Mail body 1\n"
    assert fake.read


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
    assert err.startswith("uspmp: error: ") and "Mailbox locked" in err
    assert "Traceback" not in err
    assert service.open_connections == 0


def test_debug_shows_full_exception(cert, fake_adapters, service, monkeypatch):
    monkeypatch.setattr(cli, "setup_logging", lambda: None)  # keep global logging config untouched
    service.fail_next("QueryDeliveries", "123", "Mailbox locked")

    with pytest.raises(ValueError, match="Mailbox locked"):
        run_cli("--client-certificate", cert, "--client-password", "", "--debug")


def test_certificate_load_failure_is_reported(capsys, cert):
    """Real adapter, empty dummy .p12: fails cleanly instead of with a traceback."""
    assert run_cli("--client-certificate", cert, "--client-password", "") == 1

    assert capsys.readouterr().err.startswith("uspmp: error: Failed to initialize USP_MP_Adapter")


def test_sys_argv_is_used_by_default(monkeypatch, cert, fake_adapters, service):
    monkeypatch.setattr("sys.argv", ["uspmp", "--client-certificate", cert, "--client-password", ""])

    assert cli.main() == 0


@pytest.mark.xfail(strict=True, reason="critique B8: a second --save run into the same directory fails")
def test_save_twice_into_same_directory(tmp_path, cert, fake_adapters, service):
    service.add_delivery()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    args = ("--client-certificate", cert, "--client-password", "", "--save", "--download", str(out_dir))

    assert run_cli(*args) == 0
    assert run_cli(*args, "--all") == 0
