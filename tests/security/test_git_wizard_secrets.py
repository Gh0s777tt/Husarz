"""Niezmienniki kreatora połączeń Git: gdzie token trafia, a gdzie NIGDY.

Kreator jest jedynym miejscem w API, przez które materiał sekretu wchodzi do Husarza.
Testujemy SKUTEK na realnych artefaktach — treść odpowiedzi HTTP, plik połączeń i plik
dziennika audytu — a nie to, że wywołano właściwą metodę.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.git import GitConnection, GitProviderKind, GitService
from husarz.git.connections import FileGitConnectionStore
from husarz.security import AuditLog
from husarz.security.secret_store import EncryptedFileSecretStore, build_secret_store

pytestmark = pytest.mark.security

_TOKEN = "ghp_TAJNY_TOKEN_KTORY_NIE_MOZE_WYCIEC_9876543210"


class _DictSecrets:
    """Dostawca klucza głównego magazynu (zastępuje ENV/Vault)."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz główny dla umówionej referencji."""
        return "klucz-glowny-testowy" if ref == "env:KLUCZ" else None


def _magazyn(tmp_path: Path) -> EncryptedFileSecretStore:
    return build_secret_store(
        path=tmp_path / "sekrety" / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )


def _klient(
    repo_config_dir: Path,
    tmp_path: Path,
    *,
    magazyn: EncryptedFileSecretStore | None,
    git_service: GitService | None = None,
) -> tuple[TestClient, Path, GitService]:
    """Klient API z trwałym magazynem połączeń i dziennikiem audytu na dysku."""
    plik_polaczen = tmp_path / "connections.json"
    svc = git_service or GitService(store=FileGitConnectionStore(plik_polaczen))
    plik_audytu = tmp_path / "audit.jsonl"
    app = create_app(
        load_config(repo_config_dir),
        audit=AuditLog(path=plik_audytu),
        git_service=svc,
        secret_store=magazyn,
    )
    return TestClient(app), plik_polaczen, svc


def _zadanie(nazwa: str = "gh") -> dict[str, Any]:
    return {
        "name": nazwa,
        "provider": "github",
        "api_base": "https://api.github.com",
        "token": _TOKEN,
        "username": "acme",
    }


def test_odpowiedz_zawiera_referencje_a_nie_token(repo_config_dir: Path, tmp_path: Path) -> None:
    """Panel dostaje ``husarz:git/<nazwa>`` — materiał nie wraca do przeglądarki."""
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))

    odp = client.post("/api/git/connections/wizard", json=_zadanie())

    assert odp.status_code == 200, odp.text
    assert odp.json()["token_ref"] == "husarz:git/gh"
    assert _TOKEN not in odp.text


def test_token_nie_trafia_do_pliku_polaczen(repo_config_dir: Path, tmp_path: Path) -> None:
    """Magazyn połączeń przechowuje metadane; materiał leży wyłącznie w magazynie sekretów."""
    client, plik_polaczen, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))

    client.post("/api/git/connections/wizard", json=_zadanie())

    tresc = plik_polaczen.read_text(encoding="utf-8")
    assert _TOKEN not in tresc
    # Nośność: plik NA PEWNO opisuje to połączenie — inaczej brak tokenu nic by nie znaczył.
    assert "husarz:git/gh" in tresc


def test_token_nie_trafia_do_dziennika_audytu(repo_config_dir: Path, tmp_path: Path) -> None:
    """Dziennik audytu jest niemodyfikowalny — sekret raz w nim zapisany zostaje na zawsze."""
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))

    client.post("/api/git/connections/wizard", json=_zadanie())

    dziennik = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert _TOKEN not in dziennik
    assert "git.connection.add" in dziennik  # nośność: wpis o dodaniu jednak powstał


def test_zapisany_token_daje_sie_odczytac_przez_referencje(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Cały sens kreatora: połączenie ma DZIAŁAĆ, nie tylko wyglądać na zapisane."""
    magazyn = _magazyn(tmp_path)
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=magazyn)

    ref = client.post("/api/git/connections/wizard", json=_zadanie()).json()["token_ref"]

    assert magazyn.resolve(ref) == _TOKEN


def test_bez_magazynu_kreator_odmawia_zamiast_zapisac_gdziekolwiek(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Wyłączony magazyn = jawna odmowa z instrukcją, nigdy cichy zapis tokenu."""
    client, plik_polaczen, _ = _klient(repo_config_dir, tmp_path, magazyn=None)

    odp = client.post("/api/git/connections/wizard", json=_zadanie())

    assert odp.status_code == 409
    assert "secret_store" in odp.json()["detail"]
    assert not plik_polaczen.exists() or _TOKEN not in plik_polaczen.read_text(encoding="utf-8")


def test_kolizja_nazwy_nie_niszczy_istniejacego_sekretu(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Regresja: przy zajętej nazwie NIE wolno nadpisać ani skasować cudzego tokenu.

    Naiwna kolejność (zapisz sekret → dodaj połączenie → posprzątaj po błędzie) kasuje token
    istniejącego połączenia o tej samej nazwie. Sprawdzamy, że stary token przeżywa.
    """
    magazyn = _magazyn(tmp_path)
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=magazyn)
    client.post("/api/git/connections/wizard", json=_zadanie())
    assert magazyn.resolve("husarz:git/gh") == _TOKEN

    odp = client.post(
        "/api/git/connections/wizard", json={**_zadanie(), "token": "token-napastnika"}
    )

    assert odp.status_code == 409
    assert magazyn.resolve("husarz:git/gh") == _TOKEN, "pierwotny token został utracony"


def test_usuniecie_polaczenia_kasuje_jego_sekret(repo_config_dir: Path, tmp_path: Path) -> None:
    """Sekret nie zostaje osierocony po usunięciu połączenia, które go używało."""
    magazyn = _magazyn(tmp_path)
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=magazyn)
    client.post("/api/git/connections/wizard", json=_zadanie())

    assert client.delete("/api/git/connections/gh").status_code == 200

    assert magazyn.resolve("husarz:git/gh") is None
    assert magazyn.names() == []


def test_usuniecie_polaczenia_nie_rusza_referencji_zewnetrznej(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Sekret spod ``env:``/``vault:`` nie jest nasz — operator mógł użyć go gdzie indziej."""
    magazyn = _magazyn(tmp_path)
    magazyn.put("git/gh", "sekret-nie-do-ruszenia")
    svc = GitService(store=FileGitConnectionStore(tmp_path / "connections.json"))
    svc.add(
        GitConnection(
            name="gh",
            provider=GitProviderKind.GITHUB,
            api_base="https://api.github.com",
            token_ref="env:GH_TOKEN",  # referencja ZEWNĘTRZNA, mimo zbieżnej nazwy
            username="acme",
        )
    )
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=magazyn, git_service=svc)

    assert client.delete("/api/git/connections/gh").status_code == 200

    assert magazyn.resolve("husarz:git/gh") == "sekret-nie-do-ruszenia"


def test_pusty_token_jest_odrzucany(repo_config_dir: Path, tmp_path: Path) -> None:
    """Pusty token objawiłby się dopiero jako odmowa GitHuba — blokujemy przy wejściu."""
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))

    odp = client.post("/api/git/connections/wizard", json={**_zadanie(), "token": "   "})

    assert odp.status_code == 400


def test_za_dlugi_token_nie_wraca_w_komunikacie_bledu(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Komunikat o przekroczonej długości NIE powtarza wartości — inaczej token trafia do logu.

    To powód, dla którego pole ``token`` nie ma ograniczenia Pydantic: domyślna obsługa
    ``RequestValidationError`` w FastAPI zwraca odrzuconą wartość w polu ``input``.
    """
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))
    dlugi = "x" * 5000

    odp = client.post("/api/git/connections/wizard", json={**_zadanie(), "token": dlugi})

    assert odp.status_code == 400
    assert dlugi not in odp.text
    assert "x" * 100 not in odp.text


def test_status_magazynu_nie_ujawnia_wartosci(repo_config_dir: Path, tmp_path: Path) -> None:
    """Panel widzi nazwy wpisów i daty — nigdy materiału ani szyfrogramu."""
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))
    client.post("/api/git/connections/wizard", json=_zadanie())

    stan = client.get("/api/secrets/store")

    assert stan.status_code == 200
    dane = stan.json()
    assert dane["enabled"] is True
    assert [w["name"] for w in dane["entries"]] == ["git/gh"]
    assert _TOKEN not in stan.text
    assert "sealed" not in stan.text


def test_status_magazynu_gdy_wylaczony(repo_config_dir: Path, tmp_path: Path) -> None:
    """Panel musi wiedzieć, że kreator jest niedostępny — bez zgadywania po błędzie 409."""
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=None)

    dane = client.get("/api/secrets/store").json()

    assert dane == {"enabled": False, "entries": []}


def test_token_nie_wycieka_do_zadnego_artefaktu_na_dysku(
    repo_config_dir: Path, tmp_path: Path
) -> None:
    """Zbiorczy niezmiennik: PRZESZUKUJEMY wszystkie pliki, jakie powstały podczas operacji.

    Testy powyżej sprawdzają znane pliki. Ten łapie ścieżkę, o której nikt nie pomyślał —
    plik tymczasowy, kopię zapasową, dziennik pomocniczy.
    """
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))
    client.post("/api/git/connections/wizard", json=_zadanie())

    znalezione = [
        str(sciezka)
        for sciezka in tmp_path.rglob("*")
        if sciezka.is_file() and _TOKEN.encode("utf-8") in sciezka.read_bytes()
    ]

    assert znalezione == [], f"token znaleziony jawnym tekstem w: {znalezione}"
    # Nośność: w ogóle jakieś pliki powstały, więc pusty wynik nie bierze się z pustego katalogu.
    assert sum(1 for s in tmp_path.rglob("*") if s.is_file()) >= 3


def test_szyfrogram_faktycznie_lezy_na_dysku(repo_config_dir: Path, tmp_path: Path) -> None:
    """Dopełnienie poprzedniego testu: token JEST zapisany, tylko zaszyfrowany."""
    client, _, _ = _klient(repo_config_dir, tmp_path, magazyn=_magazyn(tmp_path))
    client.post("/api/git/connections/wizard", json=_zadanie())

    dane = json.loads((tmp_path / "sekrety" / "store.json").read_text(encoding="utf-8"))

    assert "git/gh" in dane["entries"]
    assert dane["entries"]["git/gh"]["sealed"]
