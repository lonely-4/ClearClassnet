"""Local certificate authority used to terminate TLS.

Certificate generation is synchronous because OpenSSL invokes the SNI callback
on the handshake thread, where an `asyncio.Lock` cannot be acquired. A
`threading.Lock` guards the leaf-context cache instead.
"""

from __future__ import annotations

import ipaddress
import ssl
import tempfile
import threading
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

_CA_YEARS = 10
_LEAF_DAYS = 397
_CACHE_SIZE = 256
_CA_NAME = x509.Name(
    [x509.NameAttribute(NameOID.COMMON_NAME, "ClearClassnet Local CA")]
)


class CertificateAuthority:
    """Create a CA on first use and issue per-host leaf certificates."""

    def __init__(self, directory: Path, *, cache_size: int = _CACHE_SIZE) -> None:
        self.directory = directory
        self.cert_path = directory / "ca.pem"
        self.key_path = directory / "ca.key"
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self._contexts: OrderedDict[str, ssl.SSLContext] = OrderedDict()
        self._leaves: OrderedDict[str, tuple[bytes, bytes]] = OrderedDict()
        self._ca_key: ec.EllipticCurvePrivateKey
        self._ca_cert: x509.Certificate
        self._load_or_create()

    @property
    def certificate(self) -> x509.Certificate:
        return self._ca_cert

    def leaf_for(self, hostname: str) -> tuple[bytes, bytes]:
        """Return a cached PEM certificate and private key for `hostname`."""
        cache_key = hostname.casefold()
        with self._lock:
            cached = self._leaves.get(cache_key)
            if cached is not None:
                self._leaves.move_to_end(cache_key)
                return cached
        key, cert = self._issue(hostname)
        pems = (
            cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            ),
        )
        with self._lock:
            existing = self._leaves.get(cache_key)
            if existing is not None:
                return existing
            self._leaves[cache_key] = pems
            self._evict_locked()
            return pems

    def context_for(self, hostname: str) -> ssl.SSLContext:
        """Return a cached server context whose certificate matches `hostname`."""
        cache_key = hostname.casefold()
        with self._lock:
            cached = self._contexts.get(cache_key)
            if cached is not None:
                self._contexts.move_to_end(cache_key)
                return cached
        context = _server_context(*self.leaf_for(hostname))
        with self._lock:
            existing = self._contexts.get(cache_key)
            if existing is not None:
                return existing
            self._contexts[cache_key] = context
            self._evict_locked()
            return context

    def client_context(self, default_host: str) -> ssl.SSLContext:
        """Server context that swaps in a leaf certificate during SNI.

        The handshake context is not the cached one: installing the callback on a
        cached context would make SNI replacement re-enter that callback.
        """
        context = _server_context(*self.leaf_for(default_host))

        def select_server_name(
            sslobj: ssl.SSLObject,
            server_name: str | None,
            _original: ssl.SSLContext,
        ) -> None:
            sslobj.context = self.context_for(server_name or default_host)

        context.sni_callback = select_server_name
        return context

    def _evict_locked(self) -> None:
        while len(self._contexts) > self._cache_size:
            self._contexts.popitem(last=False)
        while len(self._leaves) > self._cache_size:
            self._leaves.popitem(last=False)

    def _load_or_create(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.cert_path.is_file() and self.key_path.is_file():
            cert = x509.load_pem_x509_certificate(self.cert_path.read_bytes())
            key = serialization.load_pem_private_key(self.key_path.read_bytes(), password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise TypeError(f"CA key at {self.key_path} is not an EC key")
            self._ca_cert = cert
            self._ca_key = key
            return
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        cert = (
            x509.CertificateBuilder()
            .subject_name(_CA_NAME)
            .issuer_name(_CA_NAME)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365 * _CA_YEARS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        self.key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        self.cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self._ca_key = key
        self._ca_cert = cert

    def _issue(self, hostname: str) -> tuple[ec.EllipticCurvePrivateKey, x509.Certificate]:
        key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        common = hostname if len(hostname) <= 64 else hostname[:64]
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=_LEAF_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
        )
        san = x509.SubjectAlternativeName([_subject_alt_name(hostname)])
        builder = builder.add_extension(san, critical=False)
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(self._ca_key.public_key()),
            critical=False,
        )
        return key, builder.sign(self._ca_key, hashes.SHA256())


def _subject_alt_name(hostname: str) -> x509.GeneralName:
    try:
        return x509.IPAddress(ipaddress.ip_address(hostname))
    except ValueError:
        return x509.DNSName(hostname)


def _server_context(cert_pem: bytes, key_pem: bytes) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_alpn_protocols(["http/1.1"])
    cert_file = tempfile.NamedTemporaryFile(delete=False)
    key_file = tempfile.NamedTemporaryFile(delete=False)
    try:
        cert_file.write(cert_pem)
        key_file.write(key_pem)
        cert_file.flush()
        key_file.flush()
        cert_file.close()
        key_file.close()
        context.load_cert_chain(cert_file.name, key_file.name)
    finally:
        cert_file.close()
        key_file.close()
        Path(cert_file.name).unlink(missing_ok=True)
        Path(key_file.name).unlink(missing_ok=True)
    return context
