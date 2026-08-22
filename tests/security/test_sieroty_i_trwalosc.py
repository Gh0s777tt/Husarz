"""Zgodność trwałości dwóch magazynów, sieroty i przebudowa serwisu Git.

**Skąd te testy.** Drugi adwersaryjny przegląd (commity 1bb2191 i 5277d49) zgłosił trzy wady,
które sprawdziłem osobno i **wszystkie potwierdziłem uruchomieniem**:

1. Domknięcie fabryki serwisu Git łapało serwis z chwili STARTU. Gdy Git był wtedy wyłączony
   (a domyślnie jest), każda przebudowa po nadpisaniu runtime tworzyła PUSTY magazyn
   i kasowała połączenia dodane przez API — token zostawał na dysku jako sierota.
2. `FileGitConnectionStore` mutował pamięć PRZED zapisem i wypuszczał surowy ``OSError``.
   Kreator łapie ``GitConnectionError``, więc awaria zapisu dawała 500 i POMIJAŁA sprzątanie
   świeżo zapisanego sekretu.
3. Sekret jest zawsze trwały, a magazyn połączeń domyślnie ULOTNY — kreator produkował więc
   sierotę przy każdym restarcie, a `DELETE` zwracał `ok: true`, nie usuwając niczego.

Opis i przebieg weryfikacji: `docs/BEZPIECZENSTWO.md`, sekcja „Etap 17e".
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.git import GitService, build_git_service
from husarz.git.connections import (
    FileGitConnectionStore,
    InMemoryGitConnectionStore,
)
from husarz.git.errors import GitConnectionError
from husarz.git.models import GitConnection, GitProviderKind
from husarz.security import AuditLog
from husarz.security.secret_store import EncryptedFileSecretStore, build_secret_store

pytestmark = pytest.mark.security

_TOKEN = "ghp_TOKEN_SIEROTY_1234567890"
_BAZA = {"name": "gh", "provider": "github", "api_base": "https://api.github.com"}
_MAG = {"secret_store": {"enabled": True, "key_ref": "env:KLUCZ"}}


class _DictSecrets:
    """Dostawca klucza głównego magazynu."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz główny dla umówionej referencji."""
        return "klucz-glowny-testowy" if ref == "env:KLUCZ" else None


def _fake_resolve(host: str) -> list[str]:
    """Resolver testowy — testy nie odpytują DNS."""
    return ["140.82.121.6"]


def _polaczenie(nazwa: str = "gh") -> GitConnection:
    return GitConnection(
        name=nazwa,
        provider=GitProviderKind.GITHUB,
        api_base="https://api.github.com",
        token_ref=f"husarz:git/{nazwa}",
    )


# ------------------------------- 1. przebudowa serwisu nie gubi połączeń


def test_przebudowa_nie_gubi_polaczen_gdy_git_byl_wylaczony_przy_starcie(
    repo_config_dir: Path,
) -> None:
    """Regresja: fabryka MUSI dostać magazyn serwisu AKTUALNEGO, nie startowego.

    Odtworzone: Git wyłączony przy starcie → włączony nadpisaniem → połączenie dodane przez
    API → DRUGIE nadpisanie → połączenie znikało (a przy kreatorze token zostawał na dysku
    jako sierota).

    Magazyn jest tu ULOTNY i to ISTOTNE. Pierwsza wersja testu miała awaryjne przejście na
    ten sam PLIK, gdy fabryka dostawała ``None`` — połączenia wracały wtedy z dysku i test
    przechodził także BEZ poprawki, czyli nie chronił niczego. Wykryte kontrolą nośności.
    Z magazynem w pamięci utrata jest widoczna wprost.
    """
    katalog = Path(tempfile.mkdtemp())

    def factory(cfg: Any, store: Any) -> Any:
        if not cfg.git.enabled:
            return None
        return build_git_service(
            cfg.git,
            cfg.security,
            secrets=_DictSecrets(),
            resolve=_fake_resolve,
            # Brak przekazanego magazynu = ŚWIEŻY, pusty. Dokładnie to robił launcher,
            # gdy domknięcie na starcie miało `git_service is None`.
            store=store,
        )

    app = create_app(
        load_config(repo_config_dir),
        config_dir=repo_config_dir,
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=None,  # Git WYŁĄCZONY przy starcie — jak w domyślnej konfiguracji
        git_service_factory=factory,
    )
    client = TestClient(app)

    def nadpisz(extra: dict[str, Any]) -> Any:
        overrides: dict[str, Any] = {"git": {"enabled": True}}
        overrides.update(extra)
        return client.post("/api/config/runtime", json={"overrides": overrides})

    assert nadpisz({}).json()["ok"] is True
    dodanie = client.post("/api/git/connections", json={**_BAZA, "token_ref": "env:GH_TOKEN"})
    assert dodanie.status_code == 200, dodanie.text
    assert len(client.get("/api/git/connections").json()) == 1

    assert nadpisz({"platform": {"log_level": "DEBUG"}}).json()["ok"] is True

    polaczenia = client.get("/api/git/connections").json()
    assert len(polaczenia) == 1, "przebudowa skasowała połączenie dodane przez API"
    assert polaczenia[0]["name"] == "gh"


# ------------------------------- 2. magazyn połączeń: zapis przed mutacją


def _magazyn_polaczen(tmp_path: Path) -> FileGitConnectionStore:
    return FileGitConnectionStore(tmp_path / "conn.json")


def _na_dysku(tmp_path: Path) -> list[str]:
    dane = json.loads((tmp_path / "conn.json").read_text(encoding="utf-8"))
    return sorted(c["name"] for c in dane["connections"])


def test_nieudany_zapis_polaczen_nie_rozjezdza_pamieci_z_dyskiem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ta sama wada, którą domknięto w magazynie sekretów — tu została przeoczona."""
    store = _magazyn_polaczen(tmp_path)
    store.add(_polaczenie("a"))

    def wybuchowy(self: Any, connections: Any) -> None:
        raise GitConnectionError("symulowana awaria zapisu")

    monkeypatch.setattr(FileGitConnectionStore, "_persist", wybuchowy)
    with pytest.raises(GitConnectionError):
        store.add(_polaczenie("b"))
    monkeypatch.undo()

    w_pamieci = sorted(c.name for c in store.list_connections())
    assert w_pamieci == _na_dysku(tmp_path), "stan w pamięci rozjechał się z plikiem"
    assert w_pamieci == ["a"]


def test_awaria_zapisu_daje_blad_domenowy_a_nie_surowy_oserror(tmp_path: Path) -> None:
    """Kreator łapie ``GitConnectionError``; surowy ``OSError`` wymykał się jego obsłudze.

    Skutek był podwójny: 500 zamiast czytelnego błędu ORAZ pominięte sprzątanie świeżo
    zapisanego sekretu, który zostawał osierocony.
    """
    # Ścieżka wskazuje wnętrze PLIKU — `mkdir` katalogu nadrzędnego musi zawieść.
    plik = tmp_path / "to-plik"
    plik.write_text("x", encoding="utf-8")
    store = FileGitConnectionStore(plik / "podkatalog" / "conn.json")

    with pytest.raises(GitConnectionError):
        store.add(_polaczenie("a"))


def test_usuwanie_nieistniejacego_polaczenia_nie_dotyka_pliku(tmp_path: Path) -> None:
    """Nośność zmiany kolejności: `remove` nieistniejącego nadal jest idempotentne."""
    store = _magazyn_polaczen(tmp_path)
    store.add(_polaczenie("a"))
    przed = (tmp_path / "conn.json").read_bytes()

    store.remove("nie-ma")

    assert (tmp_path / "conn.json").read_bytes() == przed


def test_trwalosc_magazynow_jest_deklarowana_jawnie(tmp_path: Path) -> None:
    """Wołający sprawdza trwałość jawnie, zamiast zgadywać po typie obiektu."""
    assert InMemoryGitConnectionStore().persistent is False
    assert _magazyn_polaczen(tmp_path).persistent is True


# ------------------------------- 3. zgodność trwałości i sieroty


def _srodowisko(
    repo_config_dir: Path, *, trwaly: bool
) -> tuple[TestClient, EncryptedFileSecretStore, Path]:
    katalog = Path(tempfile.mkdtemp())
    magazyn = build_secret_store(
        path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )
    store = (
        FileGitConnectionStore(katalog / "conn.json") if trwaly else InMemoryGitConnectionStore()
    )
    app = create_app(
        load_config(repo_config_dir, runtime_overrides={"security": _MAG}),
        config_dir=repo_config_dir,
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=GitService(store=store),
        secret_store=magazyn,
    )
    return TestClient(app), magazyn, katalog


def test_kreator_odmawia_przy_ulotnym_magazynie_polaczen(repo_config_dir: Path) -> None:
    """Sekret jest zawsze trwały — przy ulotnych połączeniach to gwarantowana sierota.

    Odmawiamy z instrukcją, zamiast po cichu produkować token, o którym operator dowie się
    dopiero po restarcie: połączenie znika, materiał zostaje.
    """
    client, magazyn, _ = _srodowisko(repo_config_dir, trwaly=False)

    odp = client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})

    assert odp.status_code == 409, odp.text
    assert "connections_path" in odp.json()["detail"]
    assert magazyn.names() == [], "token zapisano mimo ulotnego magazynu połączeń"


def test_kreator_dziala_przy_trwalym_magazynie(repo_config_dir: Path) -> None:
    """Nośność: odmowa nie może dotyczyć poprawnie skonfigurowanej instalacji."""
    client, magazyn, _ = _srodowisko(repo_config_dir, trwaly=True)

    odp = client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})

    assert odp.status_code == 200, odp.text
    assert magazyn.names() == ["git/gh"]


def test_osierocony_sekret_da_sie_usunac_przez_api(repo_config_dir: Path) -> None:
    """Sierota po restarcie MUSI mieć drogę wyjścia — inaczej token zostaje na zawsze.

    Wcześniej `DELETE` zwracał `ok: true` i nie kasował niczego, bo połączenia już nie było.
    """
    client, magazyn, _ = _srodowisko(repo_config_dir, trwaly=True)
    magazyn.put("git/osierocony", "token-testowy-bez-polaczenia")
    assert magazyn.names() == ["git/osierocony"]

    odp = client.delete("/api/git/connections/osierocony")

    assert odp.status_code == 200
    assert odp.json()["secret_removed"] is True
    assert odp.json()["removed"] is False, "połączenia nie było — odpowiedź musi to mówić"
    assert magazyn.names() == []


def test_odpowiedz_delete_niesie_skutek_a_nie_samo_przyjeto(repo_config_dir: Path) -> None:
    """`ok: true` bez informacji, co zaszło, było odpowiedzią nieprawdziwą w praktyce."""
    client, magazyn, _ = _srodowisko(repo_config_dir, trwaly=True)
    client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})

    dane = client.delete("/api/git/connections/gh").json()

    assert dane == {"ok": True, "removed": True, "secret_removed": True}
    assert magazyn.names() == []


def test_referencja_zewnetrzna_nadal_nietkniete(repo_config_dir: Path) -> None:
    """Sprzątanie sierot NIE może ruszać sekretu spod `env:`/`vault:` — nie jest nasz."""
    client, magazyn, katalog = _srodowisko(repo_config_dir, trwaly=True)
    magazyn.put("git/zewnetrzny", "sekret-nie-do-ruszenia")
    client.post(
        "/api/git/connections",
        json={**_BAZA, "name": "zewnetrzny", "token_ref": "env:GH_TOKEN"},
    )

    dane = client.delete("/api/git/connections/zewnetrzny").json()

    assert dane["removed"] is True
    assert dane["secret_removed"] is False
    assert magazyn.resolve("husarz:git/zewnetrzny") == "sekret-nie-do-ruszenia"
