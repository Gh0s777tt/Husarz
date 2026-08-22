"""Realne uzgodnienie TLS z własnym CA — dowód, że kontekst dociera do httpx.

**Po co ten test istnieje.** Testy jednostkowe w `tests/security/test_git_ca_bundle.py`
sprawdzają, że `build_ssl_context` buduje właściwy kontekst i że `build_provider` wstawia go
do transportu. Test mutacyjny pokazał, że to NIE WYSTARCZA: podmiana `verify=self._ssl_context`
na `verify=True` w `HttpxGitTransport.__call__` przechodziła przez CAŁY zestaw testów na
zielono. Cała reszta łańcucha była sprawdzona, a ostatnie ogniwo — to, które faktycznie
decyduje — nie było sprawdzone wcale.

Dlatego tutaj podnosimy PRAWDZIWY serwer TLS na loopbacku, z certyfikatem podpisanym przez
wygenerowany na miejscu urząd, i wykonujemy PRAWDZIWE uzgodnienie. Dowodem nie jest to, że
połączenie z własnym CA działa — dowodem jest to, że **bez niego zawodzi**.

Test celowo omija `build_provider`: bramka egress twardo blokuje loopback dla Gita (i słusznie),
więc sprawdzamy sam transport, konstruując `PinnedTarget` ręcznie. Warstwy egress i pinowania
mają własne, osobne testy.
"""

from __future__ import annotations

import http.server
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from husarz.git.client import HttpxGitTransport, build_ssl_context
from husarz.git.errors import GitTransportError
from husarz.ssrf import PinnedTarget

pytestmark = pytest.mark.integration

pytest.importorskip(
    "cryptography",
    reason="generowanie certyfikatów testowych wymaga extra husarz[memory] (cryptography)",
)

# Oryginalny resolver przechwycony PRZY IMPORCIE MODUŁU. Kolejność jest istotna: pytest
# importuje moduły testowe podczas zbierania, a bezpiecznik `_no_real_dns` z conftest jest
# fixture'em zakładanym dopiero przy uruchomieniu testu. Pobranie oryginału przez
# `from tests.conftest import ...` NIE działa — ładuje conftest po raz drugi, już po założeniu
# bezpiecznika, i zwraca właśnie tę zablokowaną funkcję (sprawdzone: test padał na assercji
# „Test odpytał realny DNS" dla adresu 127.0.0.1).
_PRAWDZIWY_GETADDRINFO = socket.getaddrinfo

_NAZWA_SERWERA = "git.test.wewn"


def _wystaw_ca_i_certyfikat(katalog: Path) -> tuple[Path, Path]:
    """Generuje urząd (CA) oraz podpisany przez niego certyfikat serwera.

    Args:
        katalog: Katalog na pliki PEM.

    Returns:
        Para ``(plik_ca, plik_serwera)``; plik serwera zawiera certyfikat i klucz prywatny.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    teraz = datetime.now(UTC)

    klucz_ca = ec.generate_private_key(ec.SECP256R1())
    podmiot_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "husarz-test-ca")])
    cert_ca = (
        x509.CertificateBuilder()
        .subject_name(podmiot_ca)
        .issuer_name(podmiot_ca)
        .public_key(klucz_ca.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(teraz - timedelta(days=1))
        .not_valid_after(teraz + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        # OpenSSL 3 weryfikuje łańcuch ŚCIŚLE i odrzuca certyfikaty, którym brakuje
        # rozszerzeń — kolejno dawał „Missing Authority Key Identifier", a potem
        # „CA cert does not include key usage extension". Poprawny podpis nie wystarcza,
        # więc urząd dostaje SubjectKeyIdentifier oraz KeyUsage z prawem podpisywania
        # certyfikatów, a liść — AuthorityKeyIdentifier (niżej).
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(klucz_ca.public_key()), critical=False
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
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
        .sign(klucz_ca, hashes.SHA256())
    )

    klucz_srv = ec.generate_private_key(ec.SECP256R1())
    cert_srv = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, _NAZWA_SERWERA)]))
        .issuer_name(podmiot_ca)
        .public_key(klucz_srv.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(teraz - timedelta(days=1))
        .not_valid_after(teraz + timedelta(days=1))
        # SAN jest OBOWIĄZKOWY — nowoczesne biblioteki ignorują CN przy weryfikacji nazwy.
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(_NAZWA_SERWERA)]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(klucz_ca.public_key()),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(klucz_ca, hashes.SHA256())
    )

    plik_ca = katalog / "ca.pem"
    plik_ca.write_bytes(cert_ca.public_bytes(serialization.Encoding.PEM))
    plik_srv = katalog / "serwer.pem"
    plik_srv.write_bytes(
        cert_srv.public_bytes(serialization.Encoding.PEM)
        + klucz_srv.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return plik_ca, plik_srv


class _Uchwyt(http.server.BaseHTTPRequestHandler):
    """Odpowiada stałym JSON-em; cisza w logach, żeby nie zaśmiecać wyjścia testów."""

    def do_GET(self) -> None:  # noqa: N802 - nazwa narzucona przez BaseHTTPRequestHandler
        """Zwraca minimalną poprawną odpowiedź JSON."""
        tresc = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(tresc)))
        self.end_headers()
        self.wfile.write(tresc)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Wycisza domyślne logowanie do stderr."""


@pytest.fixture
def serwer_tls(tmp_path: Path) -> Any:
    """Podnosi serwer HTTPS na loopbacku z certyfikatem podpisanym przez testowe CA.

    Yields:
        Krotka ``(port, plik_ca)``.
    """
    plik_ca, plik_srv = _wystaw_ca_i_certyfikat(tmp_path)
    kontekst = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    kontekst.load_cert_chain(certfile=str(plik_srv))

    serwer = http.server.HTTPServer(("127.0.0.1", 0), _Uchwyt)
    serwer.socket = kontekst.wrap_socket(serwer.socket, server_side=True)
    watek = threading.Thread(target=serwer.serve_forever, daemon=True)
    watek.start()
    try:
        yield serwer.server_address[1], plik_ca
    finally:
        serwer.shutdown()
        serwer.server_close()
        watek.join(timeout=5)


@pytest.fixture
def dns_odblokowany(monkeypatch: pytest.MonkeyPatch) -> None:
    """Przywraca `socket.getaddrinfo` WYŁĄCZNIE dla loopbacku.

    Globalny bezpiecznik z `tests/conftest.py` blokuje realny DNS dla całego zestawu i ma tak
    zostać. Tutaj potrzebujemy prawdziwego gniazda do 127.0.0.1, ale nie chcemy przy okazji
    otworzyć furtki na zapytania do świata — dlatego przepuszczamy wyłącznie loopback.
    """
    prawdziwy = _PRAWDZIWY_GETADDRINFO

    def _tylko_loopback(host: Any, *args: Any, **kwargs: Any) -> Any:
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"Test próbował rozwiązać nazwę spoza loopbacku: {host!r}")
        return prawdziwy(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", _tylko_loopback)


def _cel(port: int) -> PinnedTarget:
    """Cel udający przypiętą nazwę: łączymy się z IP, weryfikujemy po nazwie."""
    return PinnedTarget(
        connect_url=f"https://127.0.0.1:{port}/api/v4/projects",
        host_header=_NAZWA_SERWERA,
        sni_hostname=_NAZWA_SERWERA,
        pinned_ip="127.0.0.1",
    )


def test_polaczenie_z_wlasnym_ca_dochodzi_do_skutku(
    serwer_tls: tuple[int, Path], dns_odblokowany: None
) -> None:
    """Z własnym CA uzgodnienie TLS przechodzi i dostajemy odpowiedź serwera."""
    port, plik_ca = serwer_tls
    transport = HttpxGitTransport(ssl_context=build_ssl_context(str(plik_ca)))

    status, dane = transport("GET", _cel(port), {"User-Agent": "husarz"}, None, 10)

    assert status == 200
    assert dane == {"ok": True}


def test_bez_wlasnego_ca_to_samo_polaczenie_ZAWODZI(
    serwer_tls: tuple[int, Path], dns_odblokowany: None
) -> None:
    """DOWÓD, że to kontekst decyduje — bez niego identyczne wywołanie się nie udaje.

    To jest asercja, której brakowało: sprawdza SKUTEK przekazania kontekstu, a nie sam fakt
    jego zbudowania. Bez niej podmiana `verify=self._ssl_context` na `verify=True` przechodziła
    przez cały zestaw testów niezauważona.
    """
    port, _ = serwer_tls
    transport = HttpxGitTransport()  # magazyn systemowy — nasze CA nie jest w nim znane

    with pytest.raises(GitTransportError):
        transport("GET", _cel(port), {"User-Agent": "husarz"}, None, 10)


def test_obce_ca_nie_wystarcza(
    serwer_tls: tuple[int, Path], dns_odblokowany: None, tmp_path: Path
) -> None:
    """Kontrpróba: poprawnie zbudowany kontekst z NIEWŁAŚCIWYM urzędem też musi zawieść."""
    port, _ = serwer_tls
    katalog_obcy = tmp_path / "obce"
    katalog_obcy.mkdir()
    obce_ca, _ = _wystaw_ca_i_certyfikat(katalog_obcy)

    transport = HttpxGitTransport(ssl_context=build_ssl_context(str(obce_ca)))

    with pytest.raises(GitTransportError):
        transport("GET", _cel(port), {"User-Agent": "husarz"}, None, 10)


def test_wlasne_ca_nie_wylacza_weryfikacji_nazwy_hosta(
    serwer_tls: tuple[int, Path], dns_odblokowany: None
) -> None:
    """Certyfikat jest ważny, ale wystawiony na INNĄ nazwę — połączenie musi zawieść.

    Pilnuje, żeby własne CA nie stało się tylnymi drzwiami do akceptowania dowolnego hosta
    podpisanego przez ten urząd.
    """
    port, plik_ca = serwer_tls
    transport = HttpxGitTransport(ssl_context=build_ssl_context(str(plik_ca)))
    zly_cel = PinnedTarget(
        connect_url=f"https://127.0.0.1:{port}/api/v4/projects",
        host_header="zupelnie.inna.nazwa",
        sni_hostname="zupelnie.inna.nazwa",
        pinned_ip="127.0.0.1",
    )

    with pytest.raises(GitTransportError):
        transport("GET", zly_cel, {"User-Agent": "husarz"}, None, 10)
