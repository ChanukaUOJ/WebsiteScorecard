"""Tests for URL parsing utilities."""

import pytest

from websitescorecard.url_utils import parse_url


def test_bare_domain_defaults_to_https_port():
    parsed = parse_url("example.com")
    assert parsed.hostname == "example.com"
    assert parsed.port == 443


def test_https_url_with_path():
    parsed = parse_url("https://foo.com/some/path")
    assert parsed.hostname == "foo.com"
    assert parsed.port == 443


def test_http_url_defaults_to_port_80():
    parsed = parse_url("http://bar.com/api")
    assert parsed.hostname == "bar.com"
    assert parsed.port == 80


def test_http_url_with_explicit_port():
    parsed = parse_url("http://bar.com:8080/api")
    assert parsed.hostname == "bar.com"
    assert parsed.port == 8080


def test_whitespace_is_stripped():
    parsed = parse_url("  https://example.com  ")
    assert parsed.hostname == "example.com"


def test_empty_url_raises():
    with pytest.raises(ValueError, match="empty URL"):
        parse_url("")


def test_invalid_url_raises():
    with pytest.raises(ValueError, match="invalid URL"):
        parse_url("://bad")
