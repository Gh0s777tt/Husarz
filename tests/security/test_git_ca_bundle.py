"""Niezmienniki własnego CA dla połączeń Git (samodzielnie hostowany GitLab).

Testujemy SKUTEK na REALNYCH certyfikatach generowanych w locie, nie na deklaracji
„przekazano ścieżkę". Kluczowe pytanie brzmi: czy kontekst TLS faktycznie ufa naszemu
urzędowi i faktycznie NIE ufa nikomu innemu — bo dokładnie to odróżnia zawężone zaufanie
od globalnego rozszerzenia magazynu certyfikatów.
"""

from __future__ import annotations

import ssl
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from husarz.config.schema import EgressConfig, EgressPolicy
from husarz.git.client import HttpxGitTransport, build_provider, build_ssl_context
from husarz.git.connections import FileGitConnectionStore
from husarz.git.errors import GitError
from husarz.git.models import GitConnection, GitProviderKind

pytestmark = pytest.mark.security

cryptography = pytest.importorskip(
    "cryptography",
    reason="generowanie certyfikatów testowych wymaga extra husarz[memory] (cryptography)",
)


def _wystaw_ca(katalog: Path, nazwa: str) -> Path:
    """Generuje samopodpisany certyfikat CA i zapisuje go w PEM.

    Args:
        katalog: Katalog docelowy.
        nazwa: Nazwa pliku (bez rozszerzenia) i CN urzędu.

    Returns:
        Ścieżka do pliku PEM.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    klucz = ec.generate_private_key(ec.SECP256R1())
    podmiot = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nazwa)])
    teraz = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(podmiot)
        .issuer_name(podmiot)
        .public_key(klucz.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(teraz - timedelta(days=1))
        .not_valid_after(teraz + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(klucz, hashes.SHA256())
    )
    sciezka = katalog / f"{nazwa}.pem"
    sciezka.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return sciezka


def _polaczenie(ca_bundle: str | None = None) -> GitConnection:
    return GitConnection(
        name="wewnetrzny-gitlab",
        provider=GitProviderKind.GITLAB,
        api_base="https://git.firma.wewn/api/v4",
        token_ref="env:GL",
        ca_bundle=ca_bundle,
    )


def test_brak_ca_oznacza_magazyn_systemowy() -> None:
    """Bez wskazanego CA nic się nie zmienia — obowiązują urzędy systemowe."""
    assert build_ssl_context(None) is None
    assert build_ssl_context("") is None
    assert build_ssl_context("   ") is None


def test_wskazane_ca_jest_faktycznie_zaufane(tmp_path: Path) -> None:
    """Kontekst ufa NASZEMU urzędowi — sprawdzone na liście wczytanych certyfikatów."""
    ca = _wystaw_ca(tmp_path, "moje-ca")

    ctx = build_ssl_context(str(ca))

    assert ctx is not None
    zaufane = [c["subject"] for c in ctx.get_ca_certs()]
    assert any("moje-ca" in str(s) for s in zaufane), zaufane


def test_wlasne_ca_ZASTEPUJE_magazyn_systemowy_a_nie_go_rozszerza(tmp_path: Path) -> None:
    """Najważniejszy niezmiennik bezpieczeństwa tej funkcji.

    Gdyby bundle DOKŁADAŁ się do magazynu systemowego (semantyka ``SSL_CERT_FILE``), prywatny
    urząd zyskałby prawo poświadczania dowolnego hosta — także `api.github.com`. Zawężamy
    zaufanie: kontekst z własnym CA zna WYŁĄCZNIE ten jeden urząd.
    """
    ca = _wystaw_ca(tmp_path, "moje-ca")

    ctx = build_ssl_context(str(ca))
    systemowy = ssl.create_default_context()

    assert ctx is not None
    assert len(ctx.get_ca_certs()) == 1, "kontekst zna więcej urzędów niż wskazany plik"
    # Nośność: magazyn systemowy NA PEWNO ma ich wiele, więc powyższa asercja coś znaczy.
    assert len(systemowy.get_ca_certs()) > 1


def test_obce_ca_nie_jest_zaufane(tmp_path: Path) -> None:
    """Kontrpróba: urząd, którego NIE wskazano, nie trafia do kontekstu."""
    moje = _wystaw_ca(tmp_path, "moje-ca")
    _wystaw_ca(tmp_path, "obce-ca")

    ctx = build_ssl_context(str(moje))

    assert ctx is not None
    podmioty = str(ctx.get_ca_certs())
    assert "moje-ca" in podmioty
    assert "obce-ca" not in podmioty


def test_kontekst_zachowuje_bezpieczne_ustawienia(tmp_path: Path) -> None:
    """Własne CA nie może być tylnymi drzwiami do wyłączenia weryfikacji nazwy hosta."""
    ctx = build_ssl_context(str(_wystaw_ca(tmp_path, "moje-ca")))

    assert ctx is not None
    assert ctx.check_hostname is True
    assert ctx.verify_mode is ssl.CERT_REQUIRED


def test_nieistniejaca_sciezka_jest_bledem_a_nie_cicha_degradacja(tmp_path: Path) -> None:
    """Fail-closed: literówka w ścieżce NIE może po cichu wrócić do CA systemowych.

    Cicha degradacja dałaby błąd weryfikacji TLS przy pierwszej operacji — komunikat,
    którego nikt nie powiąże z literówką w polu formularza.
    """
    with pytest.raises(GitError) as exc:
        build_ssl_context(str(tmp_path / "nie-ma-takiego.pem"))

    assert "nie istnieje" in str(exc.value)


def test_katalog_zamiast_pliku_jest_odrzucany(tmp_path: Path) -> None:
    """``capath`` to inny mechanizm — katalog podany jako plik PEM to błąd konfiguracji."""
    with pytest.raises(GitError):
        build_ssl_context(str(tmp_path))


def test_plik_ktory_nie_jest_certyfikatem_jest_odrzucany(tmp_path: Path) -> None:
    """Wskazanie przypadkowego pliku daje czytelny błąd, nie awarię w głębi httpx."""
    smiec = tmp_path / "smiec.pem"
    smiec.write_text("to zdecydowanie nie jest certyfikat", encoding="utf-8")

    with pytest.raises(GitError) as exc:
        build_ssl_context(str(smiec))

    assert "certyfikat" in str(exc.value)


def test_komunikat_bledu_nie_ujawnia_zawartosci_pliku(tmp_path: Path) -> None:
    """Operator może omyłkowo wskazać klucz prywatny — treść NIE może wrócić w API."""
    tajny = tmp_path / "omylka.pem"
    tajny.write_text("-----BEGIN PRIVATE KEY-----\nMATERIAL-KLUCZA-XYZ\n", encoding="utf-8")

    with pytest.raises(GitError) as exc:
        build_ssl_context(str(tajny))

    assert "MATERIAL-KLUCZA-XYZ" not in str(exc.value)


def test_build_provider_przekazuje_ca_do_transportu(tmp_path: Path) -> None:
    """Ścieżka końcowa: połączenie z własnym CA buduje transport z kontekstem TLS."""
    ca = _wystaw_ca(tmp_path, "moje-ca")
    egress = EgressConfig(default_policy=EgressPolicy.ALLOW, allowlist=["git.firma.wewn"])

    provider = build_provider(
        _polaczenie(str(ca)), "token", egress, resolve=lambda host: ["10.10.0.5"]
    )

    transport = provider._t  # noqa: SLF001 - test sprawdza wpięcie zależności
    assert isinstance(transport, HttpxGitTransport)
    ctx = transport._ssl_context  # noqa: SLF001
    assert ctx is not None
    assert any("moje-ca" in str(c["subject"]) for c in ctx.get_ca_certs())


def test_bez_ca_transport_uzywa_magazynu_systemowego() -> None:
    """Połączenie bez własnego CA nie dostaje kontekstu — zachowanie sprzed zmiany."""
    egress = EgressConfig(default_policy=EgressPolicy.ALLOW, allowlist=["git.firma.wewn"])

    provider = build_provider(
        _polaczenie(None), "token", egress, resolve=lambda host: ["10.10.0.5"]
    )

    assert provider._t._ssl_context is None  # noqa: SLF001


def test_bledna_sciezka_wywraca_budowe_klienta_a_nie_polaczenie_sieciowe(tmp_path: Path) -> None:
    """Błąd pojawia się PRZED próbą połączenia — bliżej przyczyny, nie jako błąd TLS."""
    egress = EgressConfig(default_policy=EgressPolicy.ALLOW, allowlist=["git.firma.wewn"])

    with pytest.raises(GitError):
        build_provider(
            _polaczenie(str(tmp_path / "brak.pem")),
            "token",
            egress,
            resolve=lambda host: ["10.10.0.5"],
        )


def test_polaczenia_zapisane_przed_zmiana_nadal_sie_wczytuja(tmp_path: Path) -> None:
    """Zgodność wstecz: plik bez pola ``ca_bundle`` nie może unieruchomić integracji."""
    plik = tmp_path / "connections.json"
    plik.write_text(
        '{"connections": [{"name": "stare", "provider": "github",'
        ' "api_base": "https://api.github.com", "token_ref": "env:GH",'
        ' "username": null}]}',
        encoding="utf-8",
    )

    store = FileGitConnectionStore(plik)

    conn = store.get("stare")
    assert conn is not None
    assert conn.ca_bundle is None


def test_ca_przezywa_zapis_i_odczyt_magazynu(tmp_path: Path) -> None:
    """Ścieżka CA jest trwała — inaczej po restarcie połączenie przestałoby działać."""
    plik = tmp_path / "connections.json"
    FileGitConnectionStore(plik).add(_polaczenie("/etc/ssl/moje-ca.pem"))

    po_restarcie = FileGitConnectionStore(plik).get("wewnetrzny-gitlab")

    assert po_restarcie is not None
    assert po_restarcie.ca_bundle == "/etc/ssl/moje-ca.pem"
