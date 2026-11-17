"""Helpers for saving attachments: safe local file names, unique paths, atomic writes."""

from __future__ import annotations

import mimetypes
import os.path
import re
import unicodedata
from pathlib import Path

# "_" is deliberately not in the safe set, so existing underscores merge with neighbouring separators
_UNSAFE_RUN = re.compile(r"[^a-z0-9-]+")
_PLAUSIBLE_EXT = re.compile(r"\.[A-Za-z0-9]{1,10}")

MAX_STEM_LENGTH = 100
FALLBACK_STEM = "attachment"


def _ascii_fold(s: str) -> str:
    """'Rechnung März' -> 'Rechnung Marz' (drop accents, keep base letters)."""
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def sanitize_filename(name: str, mime: str) -> str:
    """Return a safe file name for an attachment called `name` with MIME type `mime`.

    The stem is lower-cased, folded to ASCII, and every run of other characters
    becomes a single ``_``. The extension is the original one if it agrees with
    `mime`, otherwise the one `mime` implies, otherwise the original one.
    """
    stem, ext = os.path.splitext(name)
    if ext and not _PLAUSIBLE_EXT.fullmatch(ext):
        stem, ext = name, ""  # "v1.2 final" has no extension, not ".2 final"
    stem = _UNSAFE_RUN.sub("_", _ascii_fold(stem).lower()).strip("_")
    stem = stem[:MAX_STEM_LENGTH].rstrip("_") or FALLBACK_STEM

    original_ext = ext.lower()

    if original_ext and mimetypes.guess_type(f"x{original_ext}", strict=False)[0] == mime:
        return stem + original_ext
    return stem + (mimetypes.guess_extension(mime, strict=False) or original_ext)


def write_atomic(path: Path | str, data: bytes) -> None:
    """Write `data` to `path` so that `path` is either the old file or the complete new one.

    The bytes go to ``<path>.part`` first, which is then renamed over `path`.
    A crash leaves at most a ``.part`` file behind, never a truncated `path`.
    """
    path = Path(path)
    part = path.with_name(path.name + ".part")
    with open(part, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)


def fsync_dir(directory: Path | str) -> None:
    """Flush a directory's entries to disk, so that renames into it survive a crash.

    A no-op on platforms that can't open directories (Windows).
    """
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def unique_path(directory: Path | str, filename: str) -> Path:
    """`directory / filename`, or with `_1`, `_2`, ... appended to the stem if that exists."""
    directory = Path(directory)
    candidate = directory / filename
    stem, ext = os.path.splitext(filename)
    n = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{n}{ext}"
        n += 1
    return candidate
