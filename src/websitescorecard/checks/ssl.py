"""SSL certificate check."""

from __future__ import annotations

import socket
import ssl
from datetime import datetime, timezone

from websitescorecard.checks.base import CheckResult
from websitescorecard.url_utils import parse_url

# OpenSSL X509_V_ERR_CERT_HAS_EXPIRED
_CERT_HAS_EXPIRED = 10


class SSLCheck:
    name = "ssl"
    column = "ssl_status"
    error_column = "ssl_error"

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    def run(self, url: str) -> CheckResult:
        try:
            parsed = parse_url(url)
        except ValueError as exc:
            return CheckResult(status="unreachable", error=str(exc))

        context = ssl.create_default_context()

        try:
            with socket.create_connection(
                (parsed.hostname, parsed.port), timeout=self.timeout
            ) as sock:
                with context.wrap_socket(sock, server_hostname=parsed.hostname) as ssock:
                    cert = ssock.getpeercert()
                    if not cert:
                        return CheckResult(
                            status="no_certificate", error="No certificate returned"
                        )

                    not_after_str = cert.get("notAfter")
                    if not not_after_str:
                        return CheckResult(
                            status="no_certificate", error="Certificate missing notAfter"
                        )

                    timestamp = ssl.cert_time_to_seconds(not_after_str)
                    not_after = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    now = datetime.now(timezone.utc)

                    if not_after < now:
                        return CheckResult(
                            status="expired", error="Certificate has expired"
                        )

                    return CheckResult(status="valid", error=None)

        except ssl.SSLCertVerificationError as exc:
            return _classify_verification_error(
                exc, parsed.hostname, parsed.port, self.timeout
            )
        except ssl.SSLError as exc:
            has_cert, _ = _probe_certificate(parsed.hostname, parsed.port, self.timeout)
            if has_cert:
                return CheckResult(status="invalid", error=str(exc))
            return CheckResult(status="no_certificate", error=str(exc))
        except (TimeoutError, socket.timeout):
            return CheckResult(status="unreachable", error="Connection timed out")
        except OSError as exc:
            return CheckResult(status="unreachable", error=str(exc))


def _classify_verification_error(
    exc: ssl.SSLCertVerificationError, hostname: str, port: int, timeout: float
) -> CheckResult:
    message = _verification_error_message(exc)
    if _is_expired_error(exc):
        return CheckResult(status="expired", error=message or "Certificate has expired")

    has_cert, not_after = _probe_certificate(hostname, port, timeout)
    if has_cert and not_after is not None and not_after < datetime.now(timezone.utc):
        return CheckResult(status="expired", error=message or "Certificate has expired")

    if has_cert:
        return CheckResult(status="invalid", error=message)

    return CheckResult(status="no_certificate", error=message or str(exc))


def _is_expired_error(exc: ssl.SSLCertVerificationError) -> bool:
    if getattr(exc, "verify_code", None) == _CERT_HAS_EXPIRED:
        return True

    message = _verification_error_message(exc).lower()
    return "certificate has expired" in message or "cert has expired" in message


def _verification_error_message(exc: ssl.SSLCertVerificationError) -> str:
    verify_message = getattr(exc, "verify_message", None)
    if verify_message:
        return verify_message
    return str(exc)


def _probe_certificate(
    hostname: str, port: int, timeout: float
) -> tuple[bool, datetime | None]:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                der = ssock.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return False, None

    if not der:
        return False, None

    return True, _not_after_from_der(der)


def _not_after_from_der(der: bytes) -> datetime | None:
    times: list[datetime] = []
    i = 0
    while i < len(der) - 1:
        tag = der[i]
        if tag in (0x17, 0x18):
            length = der[i + 1]
            if length >= 0x80:
                i += 1
                continue
            if i + 2 + length > len(der):
                break
            raw = der[i + 2 : i + 2 + length].decode("ascii", errors="ignore")
            try:
                if tag == 0x17 and raw.endswith("Z") and len(raw) == 13:
                    times.append(
                        datetime.strptime(raw, "%y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
                    )
                elif tag == 0x18 and raw.endswith("Z") and len(raw) >= 15:
                    times.append(
                        datetime.strptime(raw, "%Y%m%d%H%M%SZ").replace(tzinfo=timezone.utc)
                    )
            except ValueError:
                pass
            i += 2 + length
        else:
            i += 1

    return times[-1] if times else None
