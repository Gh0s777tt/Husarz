"""`GET /api/doctor` — jedno źródło prawdy dla CLI i konsoli.

Moduł diagnozy obiecuje w swoim docstringu, że CLI i konsola WWW korzystają z tej samej
funkcji. Te testy pilnują, żeby obietnica została dotrzymana także po stronie API: te same
ustalenia, liczniki policzone z TEJ SAMEJ listy i realny adres nasłuchu w kontroli portu.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.launcher.doctor import Stan, Waga, zdiagnozuj
from husarz.security import AuditLog

pytestmark = pytest.mark.unit


class _Sonda:
    """Sonda testowa o sterowanym zachowaniu (bez sieci i bez systemu plików)."""

    def __init__(self, *, modele: list[str] | None = None, zapisywalny: bool | None = True) -> None:
        self._modele = modele
        self._zapisywalny = zapisywalny

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Wolno pytać."""
        return None

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Zwraca ustawione modele."""
        return self._modele

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Zwraca ustawiony wynik."""
        return self._zapisywalny


def _client(config_dir: Path, **kwargs: Any) -> TestClient:
    kwargs.setdefault("doctor_probe", _Sonda())
    return TestClient(
        create_app(load_config(config_dir), config_dir=config_dir, audit=AuditLog(), **kwargs)
    )


def test_liczniki_zgadzaja_sie_z_wlasna_lista(repo_config_dir: Path) -> None:
    """Podsumowanie nie może przeczyć tabeli, którą samo podsumowuje.

    Dokładnie ten błąd popełniła wersja CLI na pierwszym uruchomieniu: liczyła wyłącznie
    problemy blokujące i przy problemie NIEBLOKUJĄCYM meldowała „wszystkie kontrole
    przeszły", mając wypisany problem dwie linie wyżej.
    """
    body = _client(repo_config_dir, listen_port=8000).get("/api/doctor").json()

    f = body["findings"]
    assert body["blocking"] == sum(
        1 for u in f if u["state"] == "problem" and u["severity"] == "blokujaca"
    )
    assert body["warnings"] == sum(
        1 for u in f if u["state"] == "problem" and u["severity"] != "blokujaca"
    )
    assert body["unknown"] == sum(1 for u in f if u["state"] == "nieznany")
    # Bez tego test przeszedłby także dla pustej diagnozy (0 == 0 trzy razy).
    assert body["warnings"] > 0, "konfiguracja repo ma kolidować portem — inaczej test jest pusty"


def test_API_zwraca_TE_SAME_ustalenia_co_funkcja_CLI(repo_config_dir: Path) -> None:
    """Sedno „jednego źródła prawdy": rozjazd nośnika = dwie różne oceny tej samej instalacji."""
    config = load_config(repo_config_dir)
    sonda = _Sonda()

    body = _client(repo_config_dir, doctor_probe=sonda, listen_port=8000).get("/api/doctor").json()
    wprost = zdiagnozuj(config, sonda=sonda, host="127.0.0.1", port=8000)

    assert [u["id"] for u in body["findings"]] == [u.id for u in wprost]
    assert [u["description"] for u in body["findings"]] == [u.opis for u in wprost]


def test_kontrola_portu_uzywa_REALNEGO_portu_nasluchu(repo_config_dir: Path) -> None:
    """Launcher przekazuje `--port`; bez tego panel sprawdzałby port domyślny.

    Dostarczona konfiguracja kieruje `glm-main` na `localhost:8000`, więc Husarz stojący
    na 8000 koliduje, a stojący na 9000 — nie. Test porównuje OBA przypadki: bez wersji
    „bez kolizji" przeszedłby także wtedy, gdyby kontrola zgłaszała kolizję zawsze.
    """
    z_kolizja = _client(repo_config_dir, listen_port=8000).get("/api/doctor").json()
    bez_kolizji = _client(repo_config_dir, listen_port=9000).get("/api/doctor").json()

    idy = {u["id"] for u in z_kolizja["findings"]}
    idy_bez = {u["id"] for u in bez_kolizji["findings"]}
    assert "kolizja-portu" in idy
    assert "kolizja-portu" not in idy_bez
    assert idy != idy_bez


def test_diagnoza_widzi_konfiguracje_PO_nadpisaniu_runtime(repo_config_dir: Path) -> None:
    """Sonda i konfiguracja brane per żądanie, nie zapamiętane ze startu.

    Gdyby endpoint trzymał konfigurację z chwili budowy aplikacji, operator poprawiłby
    ustawienie w panelu i zobaczył diagnozę sprzed poprawki — czyli dostałby instrukcję
    naprawy problemu, którego już nie ma.
    """
    client = _client(repo_config_dir, listen_port=9000)
    przed = client.get("/api/doctor").json()
    assert not [u for u in przed["findings"] if u["id"] == "model-husarz-local-wlaczony"]

    odp = client.post(
        "/api/config/runtime",
        json={"overrides": {"models": {"registry": {"husarz-local": {"enabled": False}}}}},
    )
    assert odp.status_code == 200, odp.text

    po = client.get("/api/doctor").json()

    wylaczony = [u for u in po["findings"] if u["id"] == "model-husarz-local-wlaczony"]
    assert wylaczony, [u["id"] for u in po["findings"]]
    assert wylaczony[0]["state"] == "problem"
    assert przed["findings"] != po["findings"]


def test_ustalenia_sa_posortowane_problemami_do_gory(repo_config_dir: Path) -> None:
    """Operator czyta od góry — kolejność z `zdiagnozuj` musi przetrwać serializację."""
    body = _client(repo_config_dir, listen_port=8000).get("/api/doctor").json()

    kolejnosc = {Stan.PROBLEM.value: 0, Stan.NIEZNANY.value: 1, Stan.OK.value: 2}
    klucze = [kolejnosc[u["state"]] for u in body["findings"]]
    assert klucze == sorted(klucze), [u["id"] for u in body["findings"]]
    assert body["findings"][0]["state"] == Stan.PROBLEM.value


def test_stan_i_waga_sa_lancuchami_znanych_wartosci(repo_config_dir: Path) -> None:
    """Konsola mapuje stan na kolor — nieznana wartość dałaby wiersz bez oznaczenia."""
    body = _client(repo_config_dir, listen_port=8000).get("/api/doctor").json()

    stany = {u["state"] for u in body["findings"]}
    wagi = {u["severity"] for u in body["findings"]}
    assert stany <= {s.value for s in Stan}, stany
    assert wagi <= {w.value for w in Waga}, wagi
