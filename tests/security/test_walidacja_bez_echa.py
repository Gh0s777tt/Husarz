"""Odpowiedź 422 NIE odsyła wartości, którą przysłał wołający.

**Skąd te testy.** Adwersaryjny przegląd wykazał, że wcześniejsza ochrona była pozorna.
Rezygnacja z ograniczeń Pydantica na polu ``token`` (żeby nie dało się dla niego wywołać
błędu walidacji) zamykała JEDEN wariant. Domyślna obsługa ``RequestValidationError``
w FastAPI zwraca ``exc.errors()``, a każdy wpis niesie pole ``input`` z odrzuconą wartością —
i wychodzi ono pięcioma innymi drogami, żadnej nie dotyczyło ograniczenie na samym polu.

Dokumentacja twierdziła wtedy, że „token nie występuje w komunikatach błędów". To było
nieprawdą; sprostowanie zapisano w `docs/BEZPIECZENSTWO.md` (Etap 17c) i w CHANGELOG-u.

Bramka jest teraz na poziomie CAŁEJ aplikacji, więc te testy chronią także endpointy,
które dopiero powstaną.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.git import GitService
from husarz.git.connections import FileGitConnectionStore
from husarz.security import AuditLog
from husarz.security.secret_store import build_secret_store

pytestmark = pytest.mark.security

_TOKEN = "ghp_TAJNY_TOKEN_KTORY_NIE_MOZE_WROCIC_9876543210"
_BAZA = {"name": "gh", "provider": "github", "api_base": "https://api.github.com"}


class _DictSecrets:
    """Dostawca klucza głównego magazynu."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz główny dla umówionej referencji."""
        return "klucz-glowny-testowy" if ref == "env:KLUCZ" else None


def _config_z_magazynem(repo_config_dir: Path) -> Any:
    """Konfiguracja z WŁĄCZONYM magazynem sekretów.

    ``create_app`` odrzuca przekazanie magazynu przy wyłączonej konfiguracji (parametr byłby
    martwy, bo bramka i tak czyta konfigurację). Testy muszą więc włączyć go tak, jak zrobiłby
    to operator — nadpisaniem, nie obejściem.
    """
    return load_config(
        repo_config_dir,
        runtime_overrides={"security": {"secret_store": {"enabled": True, "key_ref": "env:KLUCZ"}}},
    )


@pytest.fixture
def klient(repo_config_dir: Path) -> TestClient:
    """Aplikacja z włączonym magazynem sekretów i trwałym magazynem połączeń."""
    katalog = Path(tempfile.mkdtemp())
    return TestClient(
        create_app(
            _config_z_magazynem(repo_config_dir),
            audit=AuditLog(path=katalog / "audit.jsonl"),
            git_service=GitService(store=FileGitConnectionStore(katalog / "conn.json")),
            secret_store=build_secret_store(
                path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
            ),
        )
    )


def test_brak_innego_wymaganego_pola_nie_odsyla_tokenu(klient: TestClient) -> None:
    """Błąd ``missing`` niósł w ``input`` CAŁE ciało żądania — razem z tokenem."""
    cialo: dict[str, Any] = {**_BAZA, "token": _TOKEN}
    del cialo["name"]

    odp = klient.post("/api/git/connections/wizard", json=cialo)

    assert odp.status_code == 422
    assert _TOKEN not in odp.text


def test_literowka_w_nazwie_pola_nie_odsyla_tokenu(klient: TestClient) -> None:
    """``token_ref`` zamiast ``token`` na endpoincie kreatora — to samo echo ciała."""
    odp = klient.post("/api/git/connections/wizard", json={**_BAZA, "token_ref": _TOKEN})

    assert odp.status_code == 422
    assert _TOKEN not in odp.text


def test_cialo_formularzowe_nie_odsyla_tokenu(klient: TestClient) -> None:
    """Zwykłe ``curl -d`` wysyła ``x-www-form-urlencoded``; ``input`` był surowym ciałem."""
    odp = klient.post("/api/git/connections/wizard", data={**_BAZA, "token": _TOKEN})

    assert odp.status_code == 422
    assert _TOKEN not in odp.text


def test_cialo_jako_lista_nie_odsyla_tokenu(klient: TestClient) -> None:
    """Lista zamiast obiektu — ``input`` echował całą listę."""
    odp = klient.post("/api/git/connections/wizard", json=[{**_BAZA, "token": _TOKEN}])

    assert odp.status_code == 422
    assert _TOKEN not in odp.text


def test_surowy_token_w_polu_referencji_nie_wraca(klient: TestClient) -> None:
    """Najbardziej prawdopodobny wariant w praktyce.

    Operator wkleja token w pole, które oczekuje REFERENCJI. Walidator odrzuca wartość —
    i odsyłał ją z powrotem w ``input``. Ten wariant istniał przed wprowadzeniem kreatora.
    """
    odp = klient.post("/api/git/connections", json={**_BAZA, "token_ref": _TOKEN})

    assert odp.status_code == 422
    assert _TOKEN not in odp.text


def test_komunikat_nadal_mowi_co_jest_nie_tak(klient: TestClient) -> None:
    """Nośność: usunięcie ``input`` nie może zamienić błędu w bezużyteczny komunikat.

    Bez tej asercji „naprawą" byłoby zwracanie pustego ciała — token by nie wyciekał,
    ale API przestałoby być używalne.
    """
    odp = klient.post("/api/git/connections", json={**_BAZA, "token_ref": _TOKEN})
    tresc = odp.text

    assert "token_ref" in tresc, "brak wskazania POLA, którego dotyczy błąd"
    assert "referencj" in tresc.lower(), "brak wyjaśnienia, CO jest nie tak"
    assert "value_error" in tresc, "brak maszynowego typu błędu"


def test_pole_input_zniklo_z_kazdego_wpisu(klient: TestClient) -> None:
    """Sprawdzenie strukturalne, nie tylko brak konkretnego napisu.

    Test szukający wyłącznie wartości tokenu przechodziłby także wtedy, gdyby ``input``
    wracało dla innych pól — a wtedy pierwszy nowy endpoint z sekretem znów by wyciekał.
    """
    odp = klient.post("/api/git/connections/wizard", json={**_BAZA, "token_ref": _TOKEN})

    wpisy = odp.json()["detail"]
    assert isinstance(wpisy, list) and wpisy
    for wpis in wpisy:
        assert "input" not in wpis, wpis
        assert "ctx" not in wpis, wpis
        assert set(wpis) == {"type", "loc", "msg"}, wpis


def test_zwykle_bledy_walidacji_nadal_daja_422(klient: TestClient) -> None:
    """Handler nie może zmienić kodu odpowiedzi dla zwykłych błędów."""
    odp = klient.post("/api/git/connections", json={**_BAZA, "provider": "bitbucket"})

    assert odp.status_code == 422
    assert "provider" in odp.text
