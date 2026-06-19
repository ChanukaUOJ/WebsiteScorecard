"""Tests for SSL certificate check."""

import ssl
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from websitescorecard.checks.ssl import (
    SSLCheck,
    _is_expired_error,
    _not_after_from_der,
    _verification_error_message,
)


def test_empty_url_returns_unreachable():
    check = SSLCheck(timeout=1)
    result = check.run("")
    assert result.status == "unreachable"
    assert result.error is not None


def test_is_expired_error_detects_expired_messages():
    exc = type("E", (), {"__str__": lambda self: "certificate has expired"})()
    assert _is_expired_error(exc) is True  # type: ignore[arg-type]


def test_is_expired_error_detects_verify_code():
    exc = ssl.SSLCertVerificationError(1, "certificate verify failed")
    exc.verify_code = 10  # type: ignore[attr-defined]
    assert _is_expired_error(exc) is True


def test_verification_error_message_prefers_verify_message():
    exc = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1007)"
    )
    exc.verify_message = "Hostname mismatch, certificate is not valid for 'example.com'."  # type: ignore[attr-defined]
    assert (
        _verification_error_message(exc)
        == "Hostname mismatch, certificate is not valid for 'example.com'."
    )


def test_not_after_from_der_parses_utc_time():
    # UTCTime 150412235959Z = Apr 12 23:59:59 2015 GMT
    der = bytes([0x17, 0x0D]) + b"150412235959Z"
    not_after = _not_after_from_der(der)
    assert not_after == datetime(2015, 4, 12, 23, 59, 59, tzinfo=timezone.utc)


def test_not_after_from_der_handles_truncated_buffer():
    assert _not_after_from_der(b"\x17") is None
    assert _not_after_from_der(b"\x17\x0D") is None
    assert _not_after_from_der(b"\x17\x0Dshort") is None


def test_not_after_from_der_returns_last_time():
    first = bytes([0x17, 0x0D]) + b"150412235959Z"
    second = bytes([0x18, 0x0F]) + b"20301231235959Z"
    der = first + b"\x00" * 4 + second
    not_after = _not_after_from_der(der)
    assert not_after == datetime(2030, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_valid_certificate(mock_connect):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    not_after = future.strftime("%b %d %H:%M:%S %Y GMT")

    mock_sock = MagicMock()
    mock_ssock = MagicMock()
    mock_ssock.getpeercert.return_value = {"notAfter": not_after}
    mock_ssock.__enter__ = MagicMock(return_value=mock_ssock)
    mock_ssock.__exit__ = MagicMock(return_value=False)
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)

    mock_connect.return_value = mock_sock

    with patch("websitescorecard.checks.ssl.ssl.create_default_context") as mock_ctx:
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = mock_ssock
        mock_ctx.return_value = mock_context

        check = SSLCheck(timeout=1)
        result = check.run("example.com")

    assert result.status == "valid"
    assert result.error is None


@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_expired_certificate(mock_connect):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    not_after = past.strftime("%b %d %H:%M:%S %Y GMT")

    mock_sock = MagicMock()
    mock_ssock = MagicMock()
    mock_ssock.getpeercert.return_value = {"notAfter": not_after}
    mock_ssock.__enter__ = MagicMock(return_value=mock_ssock)
    mock_ssock.__exit__ = MagicMock(return_value=False)
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)

    mock_connect.return_value = mock_sock

    with patch("websitescorecard.checks.ssl.ssl.create_default_context") as mock_ctx:
        mock_context = MagicMock()
        mock_context.wrap_socket.return_value = mock_ssock
        mock_ctx.return_value = mock_context

        check = SSLCheck(timeout=1)
        result = check.run("example.com")

    assert result.status == "expired"
    assert result.error == "Certificate has expired"


@patch("websitescorecard.checks.ssl.socket.create_connection", side_effect=OSError("Connection refused"))
def test_connection_error_returns_unreachable(mock_connect):
    check = SSLCheck(timeout=1)
    result = check.run("bad.example")
    assert result.status == "unreachable"
    assert "Connection refused" in (result.error or "")


@patch("websitescorecard.checks.ssl._probe_certificate")
@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_generic_verification_error_detects_expired_via_unverified_fetch(
    mock_connect, mock_probe
):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    mock_probe.return_value = (True, past)

    mock_sock = MagicMock()
    mock_context = MagicMock()
    mock_context.wrap_socket.side_effect = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1007)"
    )

    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = mock_sock

    with patch("websitescorecard.checks.ssl.ssl.create_default_context") as mock_ctx:
        mock_ctx.return_value = mock_context

        check = SSLCheck(timeout=1)
        result = check.run("example.com")

    assert result.status == "expired"
    assert result.error is not None


@patch("websitescorecard.checks.ssl._probe_certificate")
@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_verification_error_uses_verify_message_for_non_expired(
    mock_connect, mock_probe
):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    mock_probe.return_value = (True, future)

    mock_sock = MagicMock()
    mock_context = MagicMock()
    exc = ssl.SSLCertVerificationError(
        1, "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed (_ssl.c:1007)"
    )
    exc.verify_message = "Hostname mismatch, certificate is not valid for 'example.com'."  # type: ignore[attr-defined]
    mock_context.wrap_socket.side_effect = exc

    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = mock_sock

    with patch("websitescorecard.checks.ssl.ssl.create_default_context") as mock_ctx:
        mock_ctx.return_value = mock_context

        check = SSLCheck(timeout=1)
        result = check.run("example.com")

    assert result.status == "invalid"
    assert result.error == "Hostname mismatch, certificate is not valid for 'example.com'."


@patch("websitescorecard.checks.ssl._probe_certificate")
@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_ssl_error_with_cert_returns_invalid(mock_connect, mock_probe):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    mock_probe.return_value = (True, future)

    mock_sock = MagicMock()
    mock_context = MagicMock()
    mock_context.wrap_socket.side_effect = ssl.SSLError("bad protocol version")
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = mock_sock

    with patch("websitescorecard.checks.ssl.ssl.create_default_context") as mock_ctx:
        mock_ctx.return_value = mock_context

        check = SSLCheck(timeout=1)
        result = check.run("example.com")

    assert result.status == "invalid"
    assert "bad protocol version" in (result.error or "")


@patch("websitescorecard.checks.ssl._probe_certificate")
@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_ssl_error_without_cert_returns_no_certificate(mock_connect, mock_probe):
    mock_probe.return_value = (False, None)

    mock_sock = MagicMock()
    mock_context = MagicMock()
    mock_context.wrap_socket.side_effect = ssl.SSLError("no shared cipher")
    mock_sock.__enter__ = MagicMock(return_value=mock_sock)
    mock_sock.__exit__ = MagicMock(return_value=False)
    mock_connect.return_value = mock_sock

    with patch("websitescorecard.checks.ssl.ssl.create_default_context") as mock_ctx:
        mock_ctx.return_value = mock_context

        check = SSLCheck(timeout=1)
        result = check.run("example.com")

    assert result.status == "no_certificate"
    assert "no shared cipher" in (result.error or "")
