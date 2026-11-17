"""usp_mp.util: safe local file names and atomic writes for attachments."""

import os

import pytest

from usp_mp.util import MAX_STEM_LENGTH, sanitize_filename, unique_path, write_atomic


@pytest.mark.parametrize(
    ("name", "mime", "expected"),
    [
        # plain cases
        ("Bescheid.pdf", "application/pdf", "bescheid.pdf"),
        ("mailbody", "text/plain", "mailbody.txt"),
        # runs of unsafe characters become a single underscore, none at the ends
        ("a   b.pdf", "application/pdf", "a_b.pdf"),
        ("a___b.pdf", "application/pdf", "a_b.pdf"),
        ("  (Kopie) Antrag!!.pdf", "application/pdf", "kopie_antrag.pdf"),
        ("keep-dashes_and_underscores.pdf", "application/pdf", "keep-dashes_and_underscores.pdf"),
        # Unicode is folded to ASCII where possible
        ("Rechnung März 2026.pdf", "application/pdf", "rechnung_marz_2026.pdf"),
        ("Größe.pdf", "application/pdf", "groe.pdf"),
        # path components can't escape the target directory
        ("../../etc/passwd", "text/plain", "etc_passwd.txt"),
        ("C:\\Windows\\evil.pdf", "application/pdf", "c_windows_evil.pdf"),
        # empty stem falls back
        ("###.pdf", "application/pdf", "attachment.pdf"),
        ("", "application/pdf", "attachment.pdf"),
        # original extension kept when it agrees with the MIME type
        ("scan.jpeg", "image/jpeg", "scan.jpeg"),
        ("REPORT.PDF", "application/pdf", "report.pdf"),
        # ... replaced when it disagrees
        ("invoice.pdf", "text/plain", "invoice.txt"),
        # ... and used when the MIME type implies none
        ("data.bin", "application/x-unknown-type", "data.bin"),
        ("noext", "application/x-unknown-type", "noext"),
        # dots inside a name are not mistaken for an extension
        ("v1.2 final", "application/pdf", "v1_2_final.pdf"),
    ],
)
def test_sanitize_filename(name, mime, expected):
    assert sanitize_filename(name, mime) == expected


def test_long_names_are_truncated():
    result = sanitize_filename("x" * 300 + ".pdf", "application/pdf")

    assert result == "x" * MAX_STEM_LENGTH + ".pdf"


def test_truncation_does_not_leave_trailing_underscore():
    name = "a" * (MAX_STEM_LENGTH - 1) + " b.pdf"

    assert sanitize_filename(name, "application/pdf") == "a" * (MAX_STEM_LENGTH - 1) + ".pdf"


def test_unique_path_free_name(tmp_path):
    assert unique_path(tmp_path, "a.pdf") == tmp_path / "a.pdf"


def test_unique_path_appends_counter(tmp_path):
    (tmp_path / "a.pdf").touch()
    (tmp_path / "a_1.pdf").touch()

    assert unique_path(tmp_path, "a.pdf") == tmp_path / "a_2.pdf"


def test_unique_path_without_extension(tmp_path):
    (tmp_path / "mailbody").touch()

    assert unique_path(str(tmp_path), "mailbody") == tmp_path / "mailbody_1"


def test_write_atomic_creates_and_replaces(tmp_path):
    target = tmp_path / "a.pdf"

    write_atomic(target, b"one")
    write_atomic(target, b"two")

    assert target.read_bytes() == b"two"
    assert [p.name for p in tmp_path.iterdir()] == ["a.pdf"]


def test_write_atomic_failure_keeps_old_file(tmp_path, monkeypatch):
    target = tmp_path / "a.pdf"
    target.write_bytes(b"old")

    def failing_replace(src, dst):
        raise OSError("simulated crash before rename")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        write_atomic(target, b"new")

    assert target.read_bytes() == b"old"  # never truncated or half-written
    assert (tmp_path / "a.pdf.part").read_bytes() == b"new"
