"""`GET /api/doctor` — kto ma prawo zobaczyć diagnozę i co ona ujawnia.

**Dlaczego ten plik jest w `tests/security/`.** Diagnoza wystawiona przez HTTP dotyka
trzech powierzchni naraz: RBAC (kto pyta), ujawnienia (endpointy silników i ścieżki
katalogów operatora) oraz ruchu wychodzącego (każde wywołanie otwiera połączenia do
endpointów z konfiguracji). Kontrola egress samej sondy ma własne testy w
`tests/unit/test_doctor.py` — tutaj pilnujemy warstwy API.

Sonda jest wstrzykiwana, więc żaden z tych testów nie dotyka sieci.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from husarz.api import create_app
from husarz.config import load_config
from husarz.security import AuditLog

pytestmark = pytest.mark.security

_NAGLOWEK = {"Authorization": "Bearer s3cret"}


class _SondaCicha:
    """Sonda testowa: silnik milczy, katalogi zapisywalne. Zero ruchu sieciowego."""

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Wolno pytać (bramkę egress sprawdzają testy jednostkowe sondy)."""
        return None

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Silnik nie odpowiada — stan NIEZNANY."""
        return None

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Katalogi w porządku."""
        return True


def _client(config_dir: Path, *, rola: str, audyt: AuditLog | None = None) -> TestClient:
    app = create_app(
        load_config(config_dir),
        config_dir=config_dir,
        audit=audyt or AuditLog(),
        api_token="s3cret",
        api_role=rola,
        doctor_probe=_SondaCicha(),
    )
    return TestClient(app)


# ------------------------------------------------------------------------ RBAC


def test_rola_user_NIE_widzi_diagnozy(repo_config_dir: Path) -> None:
    """Sedno decyzji o osobnym uprawnieniu `diagnostics:read`.

    Rola `user` powstaje przy samodzielnej rejestracji i MA `config:read`. Gdyby diagnoza
    stała na `config:read`, każde publicznie założone konto odczytałoby adresy silników
    i ścieżki katalogów operatora — dane, których `/api/models` celowo nie wystawia.
    """
    client = _client(repo_config_dir, rola="user")

    assert client.get("/api/config/summary", headers=_NAGLOWEK).status_code == 200
    assert client.get("/api/doctor", headers=_NAGLOWEK).status_code == 403


def test_rola_viewer_NIE_widzi_diagnozy(repo_config_dir: Path) -> None:
    """`viewer` to podgląd; diagnoza wysyła pakiety, więc nie jest odczytem."""
    client = _client(repo_config_dir, rola="viewer")

    assert client.get("/api/audit", headers=_NAGLOWEK).status_code == 200
    assert client.get("/api/doctor", headers=_NAGLOWEK).status_code == 403


@pytest.mark.parametrize("rola", ["operator", "admin"])
def test_operator_i_admin_widza_diagnoze(repo_config_dir: Path, rola: str) -> None:
    """Nośność odmów wyżej: uprawnienie musi KOMUŚ przysługiwać.

    Bez tego przypadku test „user dostaje 403" przechodziłby także wtedy, gdyby endpoint
    był niedostępny dla wszystkich — czyli gdyby funkcja w ogóle nie działała.
    """
    client = _client(repo_config_dir, rola=rola)

    odp = client.get("/api/doctor", headers=_NAGLOWEK)

    assert odp.status_code == 200
    assert odp.json()["findings"], "diagnoza bez ustaleń nie mówi operatorowi niczego"


def test_bez_tokenu_diagnoza_jest_zamknieta(repo_config_dir: Path) -> None:
    """Przy włączonym uwierzytelnianiu brak nagłówka to 401, nie cichy dostęp."""
    client = _client(repo_config_dir, rola="operator")

    assert client.get("/api/doctor").status_code == 401


# ------------------------------------------------------- ślad w audycie i ujawnienie


def test_wywolanie_zostawia_slad_w_audycie_z_wywolujacym(repo_config_dir: Path) -> None:
    """Odczyt, który wysyła pakiety, musi mieć ślad — inaczej ruch wychodzący jest anonimowy."""
    audyt = AuditLog()
    client = _client(repo_config_dir, rola="operator", audyt=audyt)

    client.get("/api/doctor", headers=_NAGLOWEK)

    wpisy = [e for e in audyt.entries if e.action == "doctor"]
    assert len(wpisy) == 1, [e.action for e in audyt.entries]
    # Sprawdzamy WARTOŚĆ, nie samo istnienie pola: dziennik serializuje `principal` zawsze,
    # także pusty, więc asercja „pole jest" przechodziłaby bez poprawki.
    assert wpisy[0].principal == "token:operator"


def test_audyt_nie_wystawia_szczegolu_diagnozy_przez_API(repo_config_dir: Path) -> None:
    """Deny-by-default allowlisty `public_detail` obejmuje też nową akcję.

    Nowy typ wpisu nie może zacząć wyciekać swojego szczegółu przez samo powstanie —
    dopisanie do allowlisty ma być decyzją świadomą, razem z testem.
    """
    audyt = AuditLog()
    client = _client(repo_config_dir, rola="admin", audyt=audyt)
    client.get("/api/doctor", headers=_NAGLOWEK)

    widok = client.get("/api/audit", headers=_NAGLOWEK).json()

    wiersze = [e for e in widok["entries"] if e["action"] == "doctor"]
    assert wiersze, "wpis diagnozy nie dotarł do widoku audytu"
    assert wiersze[0]["detail"] == {}, wiersze[0]["detail"]


def test_szczegol_w_dzienniku_to_same_liczby(repo_config_dir: Path) -> None:
    """Dziennik jest NIEMODYFIKOWALNY — nie wkładamy do niego endpointów ani ścieżek."""
    audyt = AuditLog()
    client = _client(repo_config_dir, rola="operator", audyt=audyt)

    client.get("/api/doctor", headers=_NAGLOWEK)

    detail = next(e for e in audyt.entries if e.action == "doctor").detail
    assert set(detail) == {"blocking", "warnings", "unknown"}, detail
    assert all(isinstance(v, int) for v in detail.values()), detail
