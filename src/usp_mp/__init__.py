# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

from .adapter import USP_MP_Adapter
from .errors import ApiError, CertificateError, ChecksumError, MailboxStateError, UspMpError
from .mail import Mailbox

__all__ = [
    'ApiError',
    'CertificateError',
    'ChecksumError',
    'Mailbox',
    'MailboxStateError',
    'USP_MP_Adapter',
    'UspMpError',
]

__version__ = '1.3.20261117'
