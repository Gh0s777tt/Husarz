"""Cztery wady z zakończonej weryfikacji drugiego przeglądu.

**Skąd te testy.** Faza weryfikacji przeglądu commitów 1bb2191 i 5277d49 dokończyła się
i potwierdziła pięć zgłoszeń. Dwa dotyczyły wad naprawionych już w `cab4d12`; cztery
pozostałe są przedmiotem tego pliku. Jedno z nich to **regresja, którą wprowadziłem
w `cab4d12`** — sprzątanie sierot niszczyło działające poświadczenie.

Opis: `docs/BEZPIECZENSTWO.md`, sekcja „Etap 17f".
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
from husarz.git.errors import GitConnectionError
from husarz.git.models import GitConnection, GitProviderKind
from husarz.security import AuditLog
from husarz.security.secret_store import EncryptedFileSecretStore, build_secret_store

pytestmark = pytest.mark.security

_BAZA = {"provider": "github", "api_base": "https://api.github.com"}


class _DictSecrets:
    """Dostawca dwóch kluczy głównych — stary i nowy (do prób rotacji)."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz dla umówionych referencji."""
        return {"env:KLUCZ": "klucz-stary", "env:INNY": "klucz-nowy"}.get(ref)


def _srodowisko(
    repo_config_dir: Path,
) -> tuple[TestClient, EncryptedFileSecretStore, FileGitConnectionStore, Path]:
    katalog = Path(tempfile.mkdtemp())
    sciezka_magazynu = katalog / "store.json"
    magazyn = build_secret_store(path=sciezka_magazynu, key_ref="env:KLUCZ", secrets=_DictSecrets())
    polaczenia = FileGitConnectionStore(katalog / "conn.json")
    app = create_app(
        load_config(
            repo_config_dir,
            runtime_overrides={
                "security": {
                    "secret_store": {
                        "enabled": True,
                        "key_ref": "env:KLUCZ",
                        "path": str(sciezka_magazynu),
                    }
                }
            },
        ),
        config_dir=repo_config_dir,
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=GitService(store=polaczenia),
        secret_store=magazyn,
    )
    return TestClient(app), magazyn, polaczenia, katalog


def _nadpisz(client: TestClient, sekcja: dict[str, Any]) -> dict[str, Any]:
    odp: dict[str, Any] = client.post(
        "/api/config/runtime", json={"overrides": {"security": sekcja}}
    ).json()
    return odp


# ---------------- 1. REGRESJA z cab4d12: sprzątanie sierot niszczyło cudzy sekret


def test_delete_nie_kasuje_sekretu_uzywanego_przez_inne_polaczenie(
    repo_config_dir: Path,
) -> None:
    """Regresja wprowadzona wraz ze sprzątaniem sierot — CICHE zniszczenie poświadczenia.

    Rozszerzenie warunku na „połączenia nie ma, więc sekret jest sierotą" pomijało przypadek,
    w którym referencję współdzieli INNE połączenie (np. po zmianie nazwy). Usunięcie
    nieistniejącej nazwy kasowało wtedy działający token, a odpowiedź raportowała sukces.
    """
    client, magazyn, polaczenia, _ = _srodowisko(repo_config_dir)
    magazyn.put("git/gh", "ghp_DZIALAJACE_POSWIADCZENIE")
    polaczenia.add(
        GitConnection(
            name="produkcja",
            provider=GitProviderKind.GITHUB,
            api_base="https://api.github.com",
            token_ref="husarz:git/gh",
        )
    )

    odp = client.delete("/api/git/connections/gh")

    assert odp.status_code == 200
    assert odp.json()["secret_removed"] is False, "skasowano sekret używany przez inne połączenie"
    assert magazyn.resolve("husarz:git/gh") == "ghp_DZIALAJACE_POSWIADCZENIE"


def test_prawdziwa_sierota_nadal_da_sie_usunac(repo_config_dir: Path) -> None:
    """Nośność: poprawka nie może zabrać drogi wyjścia dla RZECZYWISTEJ sieroty."""
    client, magazyn, _, _ = _srodowisko(repo_config_dir)
    magazyn.put("git/sierota", "token-bez-polaczenia")

    odp = client.delete("/api/git/connections/sierota")

    assert odp.json()["secret_removed"] is True
    assert magazyn.names() == []


# ---------------- 2. martwe nadpisania w runtime


def test_zmiana_klucza_glownego_w_runtime_jest_odrzucana(repo_config_dir: Path) -> None:
    """Rotacja klucza przez panel kończyła się `ok: true`, a token szedł dalej starym kluczem."""
    client, _, _, katalog = _srodowisko(repo_config_dir)

    odp = _nadpisz(
        client,
        {
            "secret_store": {
                "enabled": True,
                "key_ref": "env:INNY",
                "path": str(katalog / "store.json"),
            }
        },
    )

    assert odp["ok"] is False
    assert "security.secret_store.key_ref" in odp["error"]


def test_zmiana_sciezki_magazynu_w_runtime_jest_odrzucana(repo_config_dir: Path) -> None:
    """„Przeniesienie" magazynu na inny wolumen nie tworzyło nowego pliku nigdy."""
    client, _, _, katalog = _srodowisko(repo_config_dir)

    odp = _nadpisz(
        client,
        {
            "secret_store": {
                "enabled": True,
                "key_ref": "env:KLUCZ",
                "path": str(katalog / "NOWA-store.json"),
            }
        },
    )

    assert odp["ok"] is False
    assert "security.secret_store.path" in odp["error"]
    assert not (katalog / "NOWA-store.json").exists()


def test_zmiana_sciezki_audytu_w_runtime_jest_odrzucana(repo_config_dir: Path) -> None:
    """Ten sam mechanizm dotyczy dziennika audytu — obiekt też powstaje raz przy starcie."""
    client, _, _, katalog = _srodowisko(repo_config_dir)

    odp = _nadpisz(
        client,
        {
            "secret_store": {
                "enabled": True,
                "key_ref": "env:KLUCZ",
                "path": str(katalog / "store.json"),
            },
            "audit": {"path": str(katalog / "NOWY-audit.jsonl")},
        },
    )

    assert odp["ok"] is False
    assert "security.audit" in odp["error"]
    assert not (katalog / "NOWY-audit.jsonl").exists()


def test_wylaczenie_magazynu_nadal_przechodzi(repo_config_dir: Path) -> None:
    """Nośność: bramka porównuje WARTOŚCI, nie obecność klucza w żądaniu.

    Przy wyłączaniu magazynu `key_ref` znika ze scalonej konfiguracji — i słusznie, bo
    przestaje mieć znaczenie. Wcześniejsza wersja bramki traktowała to jak zmianę i blokowała
    wyłączenie, czyli psuła kontrolę bezpieczeństwa naprawioną w Etapie 17d.
    """
    client, _, _, _ = _srodowisko(repo_config_dir)

    assert _nadpisz(client, {"secret_store": {"enabled": False}})["ok"] is True


def test_ponowne_wlaczenie_tym_samym_kluczem_przechodzi(repo_config_dir: Path) -> None:
    """Nośność: powtórzenie dotychczasowej wartości nie jest zmianą."""
    client, _, _, katalog = _srodowisko(repo_config_dir)
    _nadpisz(client, {"secret_store": {"enabled": False}})

    odp = _nadpisz(
        client,
        {
            "secret_store": {
                "enabled": True,
                "key_ref": "env:KLUCZ",
                "path": str(katalog / "store.json"),
            }
        },
    )

    assert odp["ok"] is True, odp.get("error")


def test_zwykle_nadpisanie_nadal_dziala(repo_config_dir: Path) -> None:
    """Nośność: bramka nie może blokować nadpisań, które da się zastosować."""
    client, _, _, _ = _srodowisko(repo_config_dir)

    odp = client.post(
        "/api/config/runtime", json={"overrides": {"platform": {"log_level": "DEBUG"}}}
    ).json()

    assert odp["ok"] is True, odp.get("error")


# ---------------- 3. nazwa połączenia nie może być tokenem


# Literały celowo KRÓTSZE niż prawdziwe tokeny: walidator działa po prefiksie, a pełna
# długość wyzwalałaby regułę `gitlab-pat` w gitleaks — bramka ma zostać surowa.
@pytest.mark.parametrize(
    "nazwa",
    [
        "ghp_16C7e42F292c6912E7710c838347Ae17",
        "glpat-PRZYKLAD",
        "gho_cosTam",
        "ghs_serwerowy",
        "GHP_WIELKIMI",
    ],
)
def test_nazwa_wygladajaca_na_token_jest_odrzucana(repo_config_dir: Path, nazwa: str) -> None:
    """Nazwa trafia do NIEMODYFIKOWALNEGO audytu i jako JAWNY klucz do magazynu sekretów.

    Token wklejony omyłkowo w pole nazwy byłby tam zapisany na stałe — dziennika audytu
    z definicji nie da się wyczyścić, więc jedynym wyjściem byłoby unieważnienie tokenu
    u dostawcy. Odtworzone przed poprawką: HTTP 200 i token w czterech miejscach naraz.
    """
    client, _, _, katalog = _srodowisko(repo_config_dir)

    odp = client.post(
        "/api/git/connections/wizard", json={**_BAZA, "name": nazwa, "token": "ghp_PRAWDZIWY"}
    )

    assert odp.status_code == 422, odp.text
    assert nazwa not in odp.text, "odrzucona nazwa wróciła w treści odpowiedzi"
    # Dziennik może w ogóle nie powstać — odrzucenie na walidacji nie jest zdarzeniem
    # audytowalnym. Istotne jest, że nazwa NIGDZIE się nie utrwaliła.
    dziennik = katalog / "audit.jsonl"
    assert not dziennik.exists() or nazwa not in dziennik.read_text(encoding="utf-8")


def test_ta_sama_ochrona_na_endpoincie_z_referencja(repo_config_dir: Path) -> None:
    """Obie drogi dodawania mają ten sam kontrakt — inaczej jedna zostaje furtką."""
    client, _, _, _ = _srodowisko(repo_config_dir)

    odp = client.post(
        "/api/git/connections",
        json={**_BAZA, "name": "ghp_16C7e42F292c6912E7710c838347Ae17", "token_ref": "env:GH"},
    )

    assert odp.status_code == 422


@pytest.mark.parametrize("nazwa", ["moj-github", "gh-prod-2026", "firma_gitlab", "gh.prod", "g"])
def test_sensowne_nazwy_nadal_przechodza(repo_config_dir: Path, nazwa: str) -> None:
    """Nośność: odrzucamy po PREFIKSIE, nie po heurystyce, która myliłaby się na `gh-prod`."""
    client, _, _, _ = _srodowisko(repo_config_dir)

    odp = client.post(
        "/api/git/connections/wizard", json={**_BAZA, "name": nazwa, "token": "ghp_PRAWDZIWY"}
    )

    assert odp.status_code == 200, odp.text


# ---------------- 4. awaria zapisu przy DELETE


def test_awaria_zapisu_przy_delete_daje_503_i_slad_w_audycie(
    repo_config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operacja na poświadczeniu nie może zawieść bez śladu ani surowym 500.

    Przed poprawką `GitConnectionError` z magazynu leciał niezłapany: 500 i ZERO wpisów
    w dzienniku, mimo że żądanie dotyczyło usunięcia poświadczenia.
    """
    client, magazyn, _, katalog = _srodowisko(repo_config_dir)
    client.post("/api/git/connections/wizard", json={**_BAZA, "name": "gh", "token": "ghp_X"})

    def wybuchowy(self: Any, connections: Any) -> None:
        raise GitConnectionError("symulowana awaria zapisu")

    monkeypatch.setattr(FileGitConnectionStore, "_persist", wybuchowy)
    odp = client.delete("/api/git/connections/gh")
    monkeypatch.undo()

    assert odp.status_code == 503, odp.text
    wpisy = [
        json.loads(linia)
        for linia in (katalog / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if linia.strip()
    ]
    assert any(
        w["action"] == "git.connection.remove.failed" for w in wpisy
    ), "awaria usuwania poświadczenia nie zostawiła śladu w audycie"
    # Stan pozostaje SPÓJNY: magazyn utrwala przed podmianą, więc nic nie zniknęło.
    assert magazyn.names() == ["git/gh"]
    assert len(client.get("/api/git/connections").json()) == 1
