"""Domknięcia po przeglądzie: fail-open w runtime, nazwa połączenia, audyt, trwałość zapisu.

Każdy test odpowiada zgłoszeniu z adwersaryjnego przeglądu commita 5f4039d, które zostało
potwierdzone uruchomieniem kodu, ale odcięte przez limit weryfikacji. Wszystkie sprawdzono
osobno przed napisaniem poprawki — opis w `docs/BEZPIECZENSTWO.md`, sekcja „Etap 17d".
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
from husarz.git import GitService
from husarz.git.connections import FileGitConnectionStore
from husarz.security import AuditLog
from husarz.security.secret_store import (
    EncryptedFileSecretStore,
    SecretStoreError,
    build_secret_store,
)

pytestmark = pytest.mark.security

_TOKEN = "ghp_TOKEN_DO_DOMKNIEC_12345678"
_BAZA = {"name": "gh", "provider": "github", "api_base": "https://api.github.com"}


class _DictSecrets:
    """Dostawca klucza głównego magazynu."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz główny dla umówionej referencji."""
        return "klucz-glowny-testowy" if ref == "env:KLUCZ" else None


def _config(repo_config_dir: Path, *, wlaczony: bool = True) -> Any:
    nadpisania: dict[str, Any] = {"security": {"secret_store": {"enabled": wlaczony}}}
    if wlaczony:
        nadpisania["security"]["secret_store"]["key_ref"] = "env:KLUCZ"
    return load_config(repo_config_dir, runtime_overrides=nadpisania)


def _srodowisko(repo_config_dir: Path) -> tuple[TestClient, EncryptedFileSecretStore, Path]:
    katalog = Path(tempfile.mkdtemp())
    magazyn = build_secret_store(
        path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )
    app = create_app(
        _config(repo_config_dir),
        config_dir=repo_config_dir,
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=GitService(store=FileGitConnectionStore(katalog / "conn.json")),
        secret_store=magazyn,
    )
    return TestClient(app), magazyn, katalog


# ---------------------------------------- 1. fail-open przy nadpisaniu runtime


def _wylacz_magazyn(client: TestClient) -> Any:
    return client.post(
        "/api/config/runtime",
        json={"overrides": {"security": {"secret_store": {"enabled": False}}}},
    )


def test_wylaczenie_w_runtime_blokuje_kreator(repo_config_dir: Path) -> None:
    """Regresja fail-open: po wyłączeniu w panelu kreator NIE MOŻE dalej zapisywać tokenów.

    `POST /api/config/runtime` przebudowuje router, orkiestrator, wtyczki i serwis Gita, ale
    magazyn sekretów jest domknięciem z chwili startu. Bez czytania bieżącej konfiguracji
    wyłączenie kończyło się `ok: true`, a kreator nadal przyjmował token (sprawdzone na żywej
    instancji: HTTP 200 po wyłączeniu).
    """
    client, magazyn, _ = _srodowisko(repo_config_dir)
    assert _wylacz_magazyn(client).json()["ok"] is True

    odp = client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})

    assert odp.status_code == 409, odp.text
    assert magazyn.names() == [], "token zapisano mimo wyłączonego magazynu"


def test_wylaczenie_w_runtime_widac_w_stanie_dla_panelu(repo_config_dir: Path) -> None:
    """Panel musi widzieć stan bieżący — inaczej podpowie tryb, który już nie działa."""
    client, _, _ = _srodowisko(repo_config_dir)
    assert client.get("/api/secrets/store").json()["enabled"] is True

    _wylacz_magazyn(client)

    assert client.get("/api/secrets/store").json()["enabled"] is False


def test_ponowne_wlaczenie_dziala_bez_restartu(repo_config_dir: Path) -> None:
    """Instancja magazynu zostaje, zmienia się tylko bramka — inaczej wyłączenie byłoby
    nieodwracalne do restartu, a klucz główny bywa rozwiązywalny wyłącznie w launcherze."""
    client, magazyn, _ = _srodowisko(repo_config_dir)
    _wylacz_magazyn(client)

    client.post(
        "/api/config/runtime",
        json={
            "overrides": {"security": {"secret_store": {"enabled": True, "key_ref": "env:KLUCZ"}}}
        },
    )
    odp = client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})

    assert odp.status_code == 200, odp.text
    assert magazyn.resolve("husarz:git/gh") == _TOKEN


def test_sprzeczny_parametr_przy_konstrukcji_jest_glosny(repo_config_dir: Path) -> None:
    """Wstrzyknięty magazyn przy wyłączonej konfiguracji to parametr MARTWY — nie milczymy.

    Cicho ignorowany parametr to ta sama klasa pułapki, co `internal: true` w compose,
    które bezgłośnie wyłączało publikowanie portów.
    """
    katalog = Path(tempfile.mkdtemp())
    magazyn = build_secret_store(
        path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )

    with pytest.raises(ValueError) as exc:
        create_app(_config(repo_config_dir, wlaczony=False), secret_store=magazyn)

    assert "secret_store" in str(exc.value)


# ------------------------------------------------- 2. nazwa połączenia w URL-u


@pytest.mark.parametrize("nazwa", ["grupa/projekt", "a b", "../ucieczka", "ma?znak", "%2F"])
def test_nazwa_niebezpieczna_w_url_jest_odrzucana(repo_config_dir: Path, nazwa: str) -> None:
    """Nazwa jest SEGMENTEM ŚCIEŻKI — ukośnik czynił połączenie NIEUSUWALNYM przez API.

    Sprawdzone na żywej instancji: utworzenie zwracało 200, a `DELETE` — 404, także z `%2F`.
    Połączenie zostawało na liście i trzymało token bezterminowo.
    """
    client, _, _ = _srodowisko(repo_config_dir)

    odp = client.post("/api/git/connections/wizard", json={**_BAZA, "name": nazwa, "token": _TOKEN})

    assert odp.status_code == 422, odp.text


def test_ta_sama_walidacja_na_endpoincie_z_referencja(repo_config_dir: Path) -> None:
    """Obie drogi dodawania muszą mieć ten sam kontrakt — inaczej jedna zostaje furtką."""
    client, _, _ = _srodowisko(repo_config_dir)

    odp = client.post(
        "/api/git/connections",
        json={**_BAZA, "name": "grupa/projekt", "token_ref": "env:GH"},
    )

    assert odp.status_code == 422


@pytest.mark.parametrize("nazwa", ["moj-github", "firma_gitlab", "gh.prod", "a", "A1"])
def test_poprawne_nazwy_nadal_przechodza(repo_config_dir: Path, nazwa: str) -> None:
    """Nośność: walidacja nie może odciąć nazw, których ludzie realnie używają."""
    client, _, _ = _srodowisko(repo_config_dir)

    odp = client.post("/api/git/connections/wizard", json={**_BAZA, "name": nazwa, "token": _TOKEN})

    assert odp.status_code == 200, odp.text
    assert client.delete(f"/api/git/connections/{nazwa}").status_code == 200


# ------------------------------------------------------------- 3. ślad audytu

_TOKEN_API = "token-maszynowy-do-testow"


def _srodowisko_z_auth(repo_config_dir: Path) -> tuple[TestClient, Path]:
    """Klient z tokenem maszynowym — dopiero wtedy `principal` ma NIEPUSTĄ wartość.

    Bez uwierzytelniania `_principal_ref` zwraca pusty napis, więc asercja „pole istnieje"
    przechodziłaby także po usunięciu przekazania principala. Sprawdzamy WARTOŚĆ.
    """
    katalog = Path(tempfile.mkdtemp())
    magazyn = build_secret_store(
        path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )
    app = create_app(
        _config(repo_config_dir),
        config_dir=repo_config_dir,
        api_token=_TOKEN_API,
        api_role="operator",
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=GitService(store=FileGitConnectionStore(katalog / "conn.json")),
        secret_store=magazyn,
    )
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {_TOKEN_API}"
    return client, katalog


def _wpisy_dodania(katalog: Path) -> list[dict[str, Any]]:
    wpisy = [
        json.loads(linia)
        for linia in (katalog / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if linia.strip()
    ]
    return [w for w in wpisy if w["action"] == "git.connection.add"]


def test_wpis_audytu_kreatora_niesie_principala(repo_config_dir: Path) -> None:
    """Dziennik jest niemodyfikowalny — wpis bez wywołującego nie odpowiada na „kto wprowadził".

    Sprawdzamy WARTOŚĆ, nie obecność pola: `AuditLog` serializuje `principal` zawsze, także
    pusty, więc asercja „pole istnieje" przechodziłaby po usunięciu poprawki. Wykryte
    kontrolą nośności.
    """
    client, katalog = _srodowisko_z_auth(repo_config_dir)
    client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})

    dodania = _wpisy_dodania(katalog)

    assert len(dodania) == 1
    assert dodania[0]["principal"] == "token:operator", dodania[0]
    assert dodania[0]["detail"]["token_ref"] == "husarz:git/gh"
    assert _TOKEN not in json.dumps(dodania[0])


def test_wpis_audytu_zwyklego_dodania_tez_niesie_principala(repo_config_dir: Path) -> None:
    """Obie drogi wprowadzenia poświadczenia muszą zostawiać ten sam ślad."""
    client, katalog = _srodowisko_z_auth(repo_config_dir)
    client.post("/api/git/connections", json={**_BAZA, "token_ref": "env:GH"})

    dodania = _wpisy_dodania(katalog)

    assert len(dodania) == 1
    assert dodania[0]["principal"] == "token:operator", dodania[0]
    assert dodania[0]["detail"]["token_ref"] == "env:GH"


def test_usuniecie_polaczenia_tez_niesie_principala(repo_config_dir: Path) -> None:
    """Usunięcie poświadczenia jest równie istotne dla śladu, co jego wprowadzenie."""
    client, katalog = _srodowisko_z_auth(repo_config_dir)
    client.post("/api/git/connections/wizard", json={**_BAZA, "token": _TOKEN})
    client.delete("/api/git/connections/gh")

    wpisy = [
        json.loads(linia)
        for linia in (katalog / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if linia.strip()
    ]
    usuniecia = [w for w in wpisy if w["action"] == "git.connection.remove"]

    assert len(usuniecia) == 1
    assert usuniecia[0]["principal"] == "token:operator"


# ------------------------------------------------- 4. trwałość i spójność zapisu


def _magazyn(tmp_path: Path) -> EncryptedFileSecretStore:
    return build_secret_store(
        path=tmp_path / "s" / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )


def _stan_dysku(tmp_path: Path) -> list[str]:
    return sorted(
        json.loads((tmp_path / "s" / "store.json").read_text(encoding="utf-8"))["entries"]
    )


def test_nieudany_zapis_nie_rozjezdza_pamieci_z_dyskiem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regresja: mutacja stanu PRZED udanym zapisem zostawiała magazyn rozjechany.

    Proces widział sekret, którego w pliku nie było — po restarcie referencja przestawała się
    rozwiązywać, choć wcześniej „działała".
    """
    store = _magazyn(tmp_path)
    store.put("a", "wartosc-A")

    def wybuchowy(self: Any, entries: Any) -> None:
        raise SecretStoreError("symulowana awaria zapisu")

    monkeypatch.setattr(EncryptedFileSecretStore, "_persist", wybuchowy)
    with pytest.raises(SecretStoreError):
        store.put("b", "wartosc-B")
    monkeypatch.undo()

    assert store.names() == _stan_dysku(tmp_path), "stan w pamięci rozjechał się z plikiem"
    assert store.names() == ["a"]


def test_nieudane_usuwanie_nie_rozjezdza_pamieci_z_dyskiem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """To samo dla usuwania: nieudany zapis nie może udawać, że sekret zniknął."""
    store = _magazyn(tmp_path)
    store.put("a", "wartosc-A")

    def wybuchowy(self: Any, entries: Any) -> None:
        raise SecretStoreError("symulowana awaria zapisu")

    monkeypatch.setattr(EncryptedFileSecretStore, "_persist", wybuchowy)
    with pytest.raises(SecretStoreError):
        store.delete("a")
    monkeypatch.undo()

    assert store.names() == _stan_dysku(tmp_path)
    assert store.resolve("husarz:a") == "wartosc-A", "sekret zniknął mimo nieudanego zapisu"


def test_usuwanie_nieistniejacego_nie_dotyka_pliku(tmp_path: Path) -> None:
    """Nośność zmiany kolejności: `delete` nieistniejącego wpisu nadal zwraca False."""
    store = _magazyn(tmp_path)
    store.put("a", "wartosc-A")
    przed = (tmp_path / "s" / "store.json").read_bytes()

    assert store.delete("nie-ma") is False
    assert (tmp_path / "s" / "store.json").read_bytes() == przed


def test_zapis_jest_synchronizowany_na_dysk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`os.replace` daje atomowość wobec czytelnika, nie trwałość wobec awarii zasilania.

    Sprawdzamy, że synchronizowany jest ZARÓWNO plik, JAK I katalog — bez tego drugiego
    w buforze zostaje wpis katalogowy wskazujący na nową nazwę.
    """
    import os

    zsynchronizowane: list[int] = []
    prawdziwy = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (zsynchronizowane.append(fd), prawdziwy(fd))[1])

    _magazyn(tmp_path).put("a", "wartosc-A")

    assert (
        len(zsynchronizowane) >= 2
    ), f"oczekiwano fsync pliku ORAZ katalogu, wywołano {len(zsynchronizowane)} raz(y)"
