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
            return CheckResult(status="no_certificate", error=str(exc))

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

                    not_after = datetime.strptime(
                        not_after_str, "%b %d %H:%M:%S %Y %Z"
                    ).replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)

                    if not_after < now:
                        return CheckResult(status="expired", error=None)

                    return CheckResult(status="valid", error=None)

        except ssl.SSLCertVerificationError as exc:
            if _is_expired_error(exc):
                return CheckResult(status="expired", error=None)

            not_after = _fetch_not_after_unverified(
                parsed.hostname, parsed.port, self.timeout
            )
            if not_after is not None and not_after < datetime.now(timezone.utc):
                return CheckResult(status="expired", error=None)

            return CheckResult(
                status="no_certificate", error=_verification_error_message(exc)
            )
        except ssl.SSLError as exc:
            return CheckResult(status="no_certificate", error=str(exc))
        except (TimeoutError, socket.timeout):
            return CheckResult(status="no_certificate", error="Connection timed out")
        except OSError as exc:
            return CheckResult(status="no_certificate", error=str(exc))


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


def _fetch_not_after_unverified(hostname: str, port: int, timeout: float) -> datetime | None:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    try:
        with socket.create_connection((hostname, port), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                der = ssock.getpeercert(binary_form=True)
    except (OSError, ssl.SSLError):
        return None

    if not der:
        return None

    return _not_after_from_der(der)


def _not_after_from_der(der: bytes) -> datetime | None:
    times: list[datetime] = []
    i = 0
    while i < len(der):
        tag = der[i]
        if tag in (0x17, 0x18):
            length = der[i + 1]
            if length >= 0x80:
                i += 1
                continue
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
        i += 1

    return times[-1] if times else None
