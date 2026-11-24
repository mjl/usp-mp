# Copyright (c) 2026, Martin J. Laubach
# SPDX-License-Identifier: BSD-3-Clause

"""The snapshot comparison helper; runs without a certificate."""

from snapshot import differences


def test_snapshot_comparison_reports_differences():
    """The comparison itself: precise paths for changed, missing and extra data."""
    expected = [{"id": "a", "subject": "x", "attachments": [{"size": 1}]}]
    actual = [{"id": "a", "subject": "y", "attachments": [{"size": 1}, {"size": 2}], "new": True}]

    assert differences(expected, actual) == [
        "deliveries[0].attachments: 2 entries, expected 1",
        "deliveries[0].new: unexpected True",
        "deliveries[0].subject: 'y', expected 'x'",
    ]


def test_identical_data_has_no_differences():
    data = [{"id": "a", "attachments": [{"checksum": {"value": "00"}}]}]

    assert differences(data, data) == []


def test_missing_key_and_nested_change():
    expected = [{"id": "a", "attachments": [{"checksum": {"value": "00"}}]}]
    actual = [{"attachments": [{"checksum": {"value": "ff"}}]}]

    assert differences(expected, actual) == [
        "deliveries[0].attachments[0].checksum.value: 'ff', expected '00'",
        "deliveries[0].id: missing (expected 'a')",
    ]
