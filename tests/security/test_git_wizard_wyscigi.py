"""Wyścigi między magazynem połączeń a magazynem sekretów (kreator + usuwanie).

**Skąd te testy.** Adwersaryjny przegląd commita 5f4039d wykazał, że pre-check kolizji nazwy
w kreatorze jest wzorcem check-then-act i pod współbieżnością nie chroni niczego: dwa
równoległe żądania o tej samej nazwie oba przechodzą sprawdzenie, drugie NADPISUJE sekret
pierwszego, jego `add` zawodzi na kolizji, a sprzątanie kasuje token ZWYCIĘZCY. Skutkiem jest
połączenie z nierozwiązywalną referencją — dokładnie ta cicha utrata poświadczenia, przed
którą pre-check miał chronić.

Okno wyścigu poszerzamy CELOWO, wstrzykując wolny magazyn: bez tego test przechodziłby albo
nie zależnie od chwilowego rozłożenia wątków, czyli byłby bezwartościowy jako regresja.
Spowalniamy WYŁĄCZNIE zależność testową — kod produkcyjny pozostaje nietknięty.
"""

from __future__ import annotations

import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.git import GitService
from husarz.git.connections import FileGitConnectionStore
from husarz.security import AuditLog
from husarz.security.secret_store import EncryptedFileSecretStore, build_secret_store

pytestmark = pytest.mark.security

_TOKEN_A = "ghp_TOKEN_ZADANIA_A_1111111111"
_TOKEN_B = "ghp_TOKEN_ZADANIA_B_2222222222"


class _DictSecrets:
    """Dostawca klucza głównego magazynu."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz główny dla umówionej referencji."""
        return "klucz-glowny-testowy" if ref == "env:KLUCZ" else None


class _WolnyMagazynSekretow:
    """Magazyn sekretów z opóźnionym zapisem — poszerza okno wyścigu.

    Deleguje wszystko do prawdziwego magazynu; jedyną różnicą jest pauza PRZED zapisem,
    dzięki której drugi wątek na pewno zdąży wejść między sprawdzenie a zapis.
    """

    def __init__(self, wlasciwy: EncryptedFileSecretStore, opoznienie: float = 0.25) -> None:
        self._w = wlasciwy
        self._opoznienie = opoznienie

    def put(self, name: str, value: str) -> str:
        """Zapisuje sekret po sztucznej pauzie."""
        time.sleep(self._opoznienie)
        return self._w.put(name, value)

    def delete(self, name: str) -> bool:
        """Usuwa sekret (bez opóźnienia)."""
        return self._w.delete(name)

    def resolve(self, ref: str) -> str | None:
        """Rozwiązuje referencję."""
        return self._w.resolve(ref)

    def names(self) -> list[str]:
        """Zwraca nazwy wpisów."""
        return self._w.names()

    def describe(self, name: str) -> dict[str, str] | None:
        """Zwraca metadane wpisu."""
        return self._w.describe(name)


class _WolnyMagazynPolaczen(FileGitConnectionStore):
    """Magazyn połączeń z pauzą PO usunięciu — poszerza okno wyścigu DELETE↔kreator.

    Strona pauzy jest istotna i pierwsze podejście miało ją odwrotnie. Pauza PRZED
    ``remove`` niczego nie odtwarza: drugie żądanie widzi wtedy jeszcze istniejące
    połączenie i odpada na pre-checku z kodem 409. Groźna jest szczelina MIĘDZY
    usunięciem połączenia a usunięciem jego sekretu — dopiero wtedy kreator zdąży
    utworzyć NOWE połączenie o tej samej nazwie wraz z sekretem, który kasujące
    żądanie zaraz skasuje.
    """

    def __init__(self, path: Path, opoznienie: float = 0.25) -> None:
        super().__init__(path)
        self._opoznienie = opoznienie

    def remove(self, name: str) -> None:
        """Usuwa połączenie, a potem czeka — otwierając szczelinę dla drugiego wątku."""
        super().remove(name)
        time.sleep(self._opoznienie)


def _srodowisko(
    repo_config_dir: Path,
    *,
    wolny_zapis: bool = False,
    wolne_usuwanie: bool = False,
) -> tuple[TestClient, EncryptedFileSecretStore]:
    katalog = Path(tempfile.mkdtemp())
    prawdziwy = build_secret_store(
        path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )
    magazyn: Any = _WolnyMagazynSekretow(prawdziwy) if wolny_zapis else prawdziwy
    polaczenia = (
        _WolnyMagazynPolaczen(katalog / "conn.json")
        if wolne_usuwanie
        else FileGitConnectionStore(katalog / "conn.json")
    )
    app = create_app(
        load_config(repo_config_dir),
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=GitService(store=polaczenia),
        secret_store=magazyn,
    )
    return TestClient(app), prawdziwy


def _zadanie(token: str) -> dict[str, Any]:
    return {
        "name": "gh",
        "provider": "github",
        "api_base": "https://api.github.com",
        "token": token,
    }


def test_dwa_rownolegle_zadania_nie_niszcza_tokenu_zwyciezcy(repo_config_dir: Path) -> None:
    """Regresja wyścigu: po dwóch równoległych żądaniach token MUSI działać.

    Bez wzajemnego wykluczania: oba przechodzą pre-check, drugie nadpisuje sekret pierwszego,
    jego `add` zawodzi na kolizji, a sprzątanie kasuje token zwycięzcy. Efekt — połączenie
    istnieje, ale referencja nie rozwiązuje się na nic.
    """
    client, magazyn = _srodowisko(repo_config_dir, wolny_zapis=True)

    with ThreadPoolExecutor(max_workers=2) as pula:
        wyniki = list(
            pula.map(
                lambda t: client.post("/api/git/connections/wizard", json=_zadanie(t)),
                [_TOKEN_A, _TOKEN_B],
            )
        )

    kody = sorted(r.status_code for r in wyniki)
    assert kody == [200, 409], f"oczekiwano jednego sukcesu i jednej kolizji, było {kody}"

    zwyciezca = next(r for r in wyniki if r.status_code == 200)
    ref = zwyciezca.json()["token_ref"]
    odczytany = magazyn.resolve(ref)

    assert odczytany is not None, "token zwycięzcy został skasowany przez przegrane żądanie"
    assert odczytany in (_TOKEN_A, _TOKEN_B)
    # Połączenie widoczne przez API MUSI wskazywać na sekret, który faktycznie istnieje.
    lista = client.get("/api/git/connections").json()
    assert len(lista) == 1
    assert magazyn.resolve(lista[0]["token_ref"]) == odczytany


def test_usuwanie_nie_kasuje_sekretu_zapisanego_w_miedzyczasie(repo_config_dir: Path) -> None:
    """Regresja: DELETE nie może skasować sekretu NOWEGO połączenia o tej samej nazwie.

    Bez zamka: DELETE odczytuje stare połączenie, kreator w międzyczasie tworzy nowe wraz
    z sekretem, a DELETE kasuje świeżo zapisany token — zostawiając połączenie
    z nierozwiązywalną referencją.
    """
    client, magazyn = _srodowisko(repo_config_dir, wolne_usuwanie=True)
    assert client.post("/api/git/connections/wizard", json=_zadanie(_TOKEN_A)).status_code == 200

    wyniki: dict[str, Any] = {}
    gotowy = threading.Event()

    def usun() -> None:
        gotowy.set()
        wyniki["delete"] = client.delete("/api/git/connections/gh")

    def dodaj_ponownie() -> None:
        gotowy.wait(timeout=5)
        # Wejdź w szczelinę PO usunięciu połączenia, a przed usunięciem jego sekretu.
        time.sleep(0.1)
        wyniki["wizard"] = client.post("/api/git/connections/wizard", json=_zadanie(_TOKEN_B))

    with ThreadPoolExecutor(max_workers=2) as pula:
        for f in [pula.submit(usun), pula.submit(dodaj_ponownie)]:
            f.result(timeout=20)

    assert wyniki["delete"].status_code == 200
    # Kluczowa asercja: KAŻDE połączenie widoczne w API ma rozwiązywalną referencję.
    # Nie przesądzamy, które żądanie wygrało — przesądzamy, że stan jest SPÓJNY.
    for c in client.get("/api/git/connections").json():
        assert (
            magazyn.resolve(c["token_ref"]) is not None
        ), f"połączenie '{c['name']}' wskazuje na nieistniejący sekret {c['token_ref']}"


def test_kolizja_bez_wspolbieznosci_nadal_zwraca_409(repo_config_dir: Path) -> None:
    """Nośność: zamek nie może „naprawić" testu przez zepsucie zwykłej ścieżki."""
    client, magazyn = _srodowisko(repo_config_dir)
    assert client.post("/api/git/connections/wizard", json=_zadanie(_TOKEN_A)).status_code == 200

    druga = client.post("/api/git/connections/wizard", json=_zadanie(_TOKEN_B))

    assert druga.status_code == 409
    assert magazyn.resolve("husarz:git/gh") == _TOKEN_A


def test_rownolegle_rozne_nazwy_dzialaja_niezaleznie(repo_config_dir: Path) -> None:
    """Zamek serializuje, ale nie może gubić żądań o RÓŻNYCH nazwach."""
    client, magazyn = _srodowisko(repo_config_dir)
    nazwy = [f"conn-{i}" for i in range(6)]

    with ThreadPoolExecutor(max_workers=6) as pula:
        wyniki = list(
            pula.map(
                lambda n: client.post(
                    "/api/git/connections/wizard",
                    json={**_zadanie(f"ghp_TOKEN_{n}"), "name": n},
                ),
                nazwy,
            )
        )

    assert [r.status_code for r in wyniki] == [200] * len(nazwy)
    for n in nazwy:
        assert magazyn.resolve(f"husarz:git/{n}") == f"ghp_TOKEN_{n}"


def test_polaczenie_ktore_przegralo_nie_zostawia_sekretu(repo_config_dir: Path) -> None:
    """Po kolizji w magazynie jest DOKŁADNIE jeden wpis — brak osieroconych sekretów."""
    client, magazyn = _srodowisko(repo_config_dir, wolny_zapis=True)

    with ThreadPoolExecutor(max_workers=2) as pula:
        list(
            pula.map(
                lambda t: client.post("/api/git/connections/wizard", json=_zadanie(t)),
                [_TOKEN_A, _TOKEN_B],
            )
        )

    assert magazyn.names() == ["git/gh"], magazyn.names()
