# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

"""Exceptions raised by usp_mp.

Everything derives from UspMpError, so ``except UspMpError`` catches all of
them. Some also derive from a builtin exception for compatibility with code
written against earlier versions, which raised plain ValueError/RuntimeError.
"""

from __future__ import annotations


class UspMpError(Exception):
    """Base class for all usp_mp errors."""


class CertificateError(UspMpError):
    """The client certificate (.p12) can't be read or used: missing file, wrong password, bad content."""


class ApiError(UspMpError, ValueError):
    """The service answered a SOAP call with an ``Error`` element."""

    def __init__(self, code: str, message: str, operation: str) -> None:
        self.code = code
        self.message = message
        self.operation = operation
        super().__init__(f"{operation} failed: [{code}] {message}")


class ChecksumError(UspMpError, ValueError):
    """A downloaded attachment doesn't match its checksum, or uses an unsupported algorithm."""


class MailboxStateError(UspMpError, RuntimeError):
    """The mailbox or connection is not open (not entered yet, or already closed)."""
