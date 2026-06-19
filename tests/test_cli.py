"""Tests for CLI helpers."""

from websitescorecard.cli import _collect_check_names


def test_collect_check_names_lowercases_and_strips():
    assert _collect_check_names("SSL", ssl=False) == ["ssl"]
    assert _collect_check_names(" SSL ", ssl=False) == ["ssl"]


def test_collect_check_names_deduplicates_with_ssl_flag():
    assert _collect_check_names("SSL", ssl=True) == ["ssl"]
