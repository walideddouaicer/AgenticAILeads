#!/usr/bin/env python3
"""
Windows TLS-interception compatibility.

Security software on this machine intercepts HTTPS with its own
certificate. Windows trusts it; Python's bundled CA list (certifi) does
not, so every `requests` call fails with CERTIFICATE_VERIFY_FAILED.

Fix: route Python's TLS verification through the OS trust store via the
`truststore` package (in requirements.txt). Certificate verification
stays fully ON — this only changes WHICH trust store is consulted.
(Equivalent of `git config http.sslBackend schannel`.)

Every engine that makes HTTPS calls imports this module and calls
enable_os_trust_store() before its first request. If truststore isn't
installed, this is a silent no-op (fine on machines without
interception).
"""


def enable_os_trust_store():
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass
