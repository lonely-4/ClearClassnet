import ssl

import pytest
from cryptography import x509
from cryptography.x509.oid import ExtensionOID

from clearclassnet.tls.ca import CertificateAuthority
from clearclassnet.tls.store import linux_followup, trust_command


def test_ca_is_created_once_and_reloaded(tmp_path) -> None:
    first = CertificateAuthority(tmp_path)
    second = CertificateAuthority(tmp_path)
    assert first.cert_path.is_file()
    assert first.key_path.is_file()
    assert first.certificate.public_bytes(serialization_pem()) == second.certificate.public_bytes(
        serialization_pem()
    )


def test_leaf_has_san_and_is_signed_by_the_ca(tmp_path) -> None:
    ca = CertificateAuthority(tmp_path)
    cert_pem, _key = ca.leaf_for("www.example.com")
    cert = x509.load_pem_x509_certificate(cert_pem)
    cert.verify_directly_issued_by(ca.certificate)
    san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    assert "www.example.com" in san.get_values_for_type(x509.DNSName)

    ip_pem, _ip_key = ca.leaf_for("127.0.0.1")
    ip_cert = x509.load_pem_x509_certificate(ip_pem)
    ip_san = ip_cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME).value
    assert str(ip_san.get_values_for_type(x509.IPAddress)[0]) == "127.0.0.1"


def test_leaf_and_context_caches_evict_oldest(tmp_path) -> None:
    ca = CertificateAuthority(tmp_path, cache_size=1)
    first_pem, _key = ca.leaf_for("one.example")
    again_pem, _again_key = ca.leaf_for("one.example")
    assert first_pem == again_pem
    first_context = ca.context_for("one.example")
    assert ca.context_for("one.example") is first_context
    ca.context_for("two.example")
    assert ca.context_for("one.example") is not first_context


def test_handshake_context_is_separate_from_the_cache(tmp_path) -> None:
    ca = CertificateAuthority(tmp_path)
    cached = ca.context_for("www.example.com")
    handshake = ca.client_context("www.example.com")
    assert handshake is not cached
    assert cached.sni_callback is None
    assert handshake.sni_callback is not None


@pytest.mark.asyncio
async def test_sni_selects_the_requested_host(tmp_path) -> None:
    ca = CertificateAuthority(tmp_path)
    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.load_verify_locations(cafile=str(ca.cert_path))
    client.set_alpn_protocols(["http/1.1"])

    async def handle(reader, writer) -> None:
        writer.write(b"ok")
        await writer.drain()
        writer.close()

    import asyncio

    server = await asyncio.start_server(
        handle,
        "127.0.0.1",
        0,
        ssl=ca.client_context("www.example.com"),
    )
    port = server.sockets[0].getsockname()[1]
    try:
        _reader, writer = await asyncio.open_connection(
            "127.0.0.1",
            port,
            ssl=client,
            server_hostname="other.example",
        )
        cert = writer.get_extra_info("peercert")
        names = {item[1] for group in cert["subjectAltName"] for item in [group]}
        assert "other.example" in names
        writer.close()
        await writer.wait_closed()
    finally:
        server.close()
        await server.wait_closed()


def test_trust_command_mentions_the_certificate(tmp_path) -> None:
    cert = tmp_path / "ca.pem"
    cert.write_text("placeholder")
    command = trust_command(cert)
    assert str(cert) in command
    assert linux_followup() is None or isinstance(linux_followup(), str)


def serialization_pem():
    from cryptography.hazmat.primitives import serialization

    return serialization.Encoding.PEM
