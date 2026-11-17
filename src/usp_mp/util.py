"""Helpers for turning server-supplied attachment names into safe local file names."""

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
