"""A stand-in for an antivirus that inspects secure connections: its own root certificate, and a local HTTPS server
whose certificate that root signs (as the antivirus re-signs every site).

The tests use it with the root in no certificate store (Studio must call that "intercepted"). The Studio trust job
in CI (Windows) also adds the root to the computer's own store, as the antivirus does, and points
CR_TEST_OS_TRUSTED_CA at the folder: then Studio must pass where certifi alone fails.

    python tests/studio_trust_lab.py make FOLDER     writes ca.pem, ca.cer (DER, for Windows), leaf.pem, leaf.key
"""
from __future__ import annotations

import datetime
import http.server
import ipaddress
import ssl
import sys
import threading
from pathlib import Path


def make(folder: Path, name: str = "CR Test Web Shield Root") -> Path:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    folder.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    usage = dict(content_commitment=False, key_encipherment=False, data_encipherment=False, key_agreement=False,
                 encipher_only=False, decipher_only=False)
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
          .not_valid_after(now + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, **usage), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
            .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=20))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False, **usage),
                           critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    pem = serialization.Encoding.PEM
    (folder / "ca.pem").write_bytes(ca.public_bytes(pem))
    (folder / "ca.cer").write_bytes(ca.public_bytes(serialization.Encoding.DER))
    (folder / "leaf.pem").write_bytes(leaf.public_bytes(pem) + ca.public_bytes(pem))
    (folder / "leaf.key").write_bytes(key.private_bytes(pem, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    return folder


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body>inspected hello</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass

    def handle(self):
        try:
            super().handle()
        except (OSError, ssl.SSLError):                        # a client that refused the certificate hung up
            pass


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):           # refused handshakes are the point, not noise
        pass


def serve(folder: Path):
    """(server, port) on 127.0.0.1, serving with the leaf the stand-in root signed. Stop it with server.shutdown()."""
    try:                                                       # the server side is always a plain context, even in a
        from truststore._ssl_constants import _original_SSLContext as plain   # process that trusts the computer
    except ImportError:
        plain = ssl.SSLContext
    context = plain(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(folder / "leaf.pem", folder / "leaf.key")
    server = _Server(("127.0.0.1", 0), _Page)
    server.socket = context.wrap_socket(server.socket, server_side=True, do_handshake_on_connect=False)
    threading.Thread(target=server.serve_forever, daemon=True, name="cr-trust-lab").start()
    return server, server.server_address[1]


if __name__ == "__main__":
    if sys.argv[1:2] != ["make"] or len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(make(Path(sys.argv[2])))
