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


def test_empty_url_returns_no_certificate():
    check = SSLCheck(timeout=1)
    result = check.run("")
    assert result.status == "no_certificate"
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


@patch("websitescorecard.checks.ssl.socket.create_connection", side_effect=OSError("Connection refused"))
def test_connection_error_returns_no_certificate(mock_connect):
    check = SSLCheck(timeout=1)
    result = check.run("bad.example")
    assert result.status == "no_certificate"
    assert "Connection refused" in (result.error or "")


@patch("websitescorecard.checks.ssl._fetch_not_after_unverified")
@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_generic_verification_error_detects_expired_via_unverified_fetch(
    mock_connect, mock_fetch_not_after
):
    past = datetime.now(timezone.utc) - timedelta(days=1)
    mock_fetch_not_after.return_value = past

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
    assert result.error is None


@patch("websitescorecard.checks.ssl._fetch_not_after_unverified")
@patch("websitescorecard.checks.ssl.socket.create_connection")
def test_verification_error_uses_verify_message_for_non_expired(
    mock_connect, mock_fetch_not_after
):
    future = datetime.now(timezone.utc) + timedelta(days=30)
    mock_fetch_not_after.return_value = future

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

    assert result.status == "no_certificate"
    assert result.error == "Hostname mismatch, certificate is not valid for 'example.com'."
