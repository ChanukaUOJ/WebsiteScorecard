"""Tests for CLI helpers."""

from websitescorecard.cli import _collect_check_names


def test_collect_check_names_lowercases_and_strips():
    assert _collect_check_names("SSL") == ["ssl"]
    assert _collect_check_names(" SSL ") == ["ssl"]


def test_collect_check_names_parses_multiple():
    assert _collect_check_names("ssl, http") == ["ssl", "http"]


def test_collect_check_names_empty_string():
    assert _collect_check_names("") == []
