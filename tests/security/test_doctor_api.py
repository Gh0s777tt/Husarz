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
from husarz.config.schema import DiagnosticsConfig
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
    """`viewer` nie dostaje diagnozy — i nie generuje przy tym ANI JEDNEGO zapytania.

    Sam kod 403 jest deklaracją: sprawdza odpowiedź, nie skutek. Gdyby bramka RBAC stała
    za sondowaniem, odmowa przychodziłaby PO odpytaniu silników — czyli konto bez uprawnienia
    i tak konsumowałoby globalny limit tempa i generowało ruch wychodzący. Liczymy więc
    zapytania sondy, tak jak przy limicie tempa.

    Powód samej granicy jest przy tym INNY, niż zapisano pierwotnie: nie „podgląd nie wysyła
    pakietów" (ten argument zniknął wraz z limitem), tylko ujawnienie aktualnej topologii —
    adresów silników, ścieżek operatora i katalogu silnika. Patrz komentarz w `rbac.py`.
    """
    sonda = _SondaZObiemaRolami()
    app = create_app(
        load_config(repo_config_dir),
        config_dir=repo_config_dir,
        audit=AuditLog(),
        api_token="s3cret",
        api_role="viewer",
        doctor_probe=sonda,
    )
    client = TestClient(app)

    assert client.get("/api/audit", headers=_NAGLOWEK).status_code == 200
    assert client.get("/api/doctor", headers=_NAGLOWEK).status_code == 403
    assert sonda.zapytane == [], "odmowa nastąpiła PO zapytaniu modelu"


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


# ------------------------------------------- sonda głęboka NIE jest wystawiona przez HTTP


class _SondaZObiemaRolami:
    """Sonda katalogu, która POTWIERDZA obecność modeli i zapisuje pytania głębokie.

    Potwierdzenie jest tu warunkiem sensowności testu, nie ozdobą. Pierwsza wersja używała
    zwykłej `_SondaCicha` i mutacja wystawiająca sondę głęboką przez API **nie zaczerwieniła
    testu**: w środowisku testowym DNS jest zablokowany, więc każda kontrola katalogu kończyła
    się stanem NIEZNANY, a sonda głęboka nie była w ogóle osiągana. Test nie mógł niczego
    wykryć — przechodził z niewłaściwego powodu.
    """

    def __init__(self) -> None:
        self.zapytane: list[str] = []

    def powod_odmowy_sondowania(self, endpoint: str) -> str | None:
        """Wolno pytać."""
        return None

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Potwierdza KAŻDY model z dostarczonej konfiguracji — kontrola katalogu kończy się OK."""
        return ["husarz", "glm-5.2", "hermes-3", "bielik-11b-v3.0-instruct"]

    def czy_zapisywalny(self, katalog: Path) -> bool | None:
        """Katalogi w porządku."""
        return True

    def zapytaj_model(self, model_id: str, spec: object) -> object:
        """Zapisuje, że ktoś zadał modelowi PRAWDZIWE pytanie. Nie powinno się zdarzyć."""
        self.zapytane.append(model_id)
        raise AssertionError("endpoint API zapytał model — sonda głęboka wyciekła do HTTP")


def test_endpoint_API_NIE_zadaje_pytania_modelowi(repo_config_dir: Path) -> None:
    """Sonda głęboka (`husarz doctor --probe`) jest ŚWIADOMIE poza API.

    Zapytanie modelu wczytuje wagi do pamięci i potrafi trwać minuty (zmierzone na realnej
    instalacji: 18,9 s przy zimnym starcie modelu 7B, 0,9 s zaraz potem). Wystawienie tego
    przez HTTP bez limitu tempa byłoby dźwignią do wyczerpania zasobów, a odpowiedź i tak
    musiałaby czekać na wagi. Diagnoza przez API zostaje przy kontroli katalogu; głęboka
    jest operacją terminala.

    Test sprawdza SKUTEK: sonda potwierdza obecność modeli, więc gdyby endpoint przekazał ją
    jako `sonda_gleboka`, pytanie POLECIAŁOBY i test by padł.
    """
    sonda = _SondaZObiemaRolami()
    app = create_app(
        load_config(repo_config_dir),
        config_dir=repo_config_dir,
        audit=AuditLog(),
        doctor_probe=sonda,
    )

    odp = TestClient(app).get("/api/doctor")

    assert odp.status_code == 200
    stany = {u["id"]: u["state"] for u in odp.json()["findings"]}
    assert any(
        s == "ok" for s in stany.values()
    ), f"test byłby pusty: żadna kontrola katalogu nie przeszła — {stany}"
    assert sonda.zapytane == [], "endpoint API zapytał model"


# ------------------------------------------------- limit tempa (dźwignia zasobowa)


class _SondaLiczaca(_SondaZObiemaRolami):
    """Sonda potwierdzająca modele i licząca, ile RAZY pytano silnik o katalog."""

    def __init__(self) -> None:
        super().__init__()
        self.zapytania: list[str] = []

    def modele_u_dostawcy(self, endpoint: str) -> list[str] | None:
        """Zapisuje wywołanie i potwierdza obecność modeli."""
        self.zapytania.append(endpoint)
        return super().modele_u_dostawcy(endpoint)


def _app_z_limitem(
    config_dir: Path, limit: int | None, sonda: object, *, na_osobe: int | None = None
) -> object:
    """Buduje aplikację z podanym limitem tempa diagnozy.

    `DiagnosticsConfig` budujemy WPROST, a nie przez `model_copy(update=...)`, bo ten
    drugi omija walidację. Kosztowało to już raz: gdy limit per wywołujący dostał wartość
    domyślną, helper produkował konfigurację (globalny 1, per osobę 3), której walidator
    nie dopuszcza — i wywracał się dopiero w konstruktorze ogranicznika. Test warstwy
    bezpieczeństwa nie powinien tworzyć stanów niemożliwych w produkcji.

    Args:
        config_dir: Katalog konfiguracji.
        limit: Limit globalny; ``None`` wyłącza ograniczanie.
        sonda: Podstawiona sonda diagnozy.
        na_osobe: Limit per wywołujący; ``None`` wyłącza ten poziom.

    Returns:
        Gotowa aplikacja.
    """
    config = load_config(config_dir)
    diagnostyka = DiagnosticsConfig(
        max_requests_per_minute=limit,
        max_requests_per_minute_per_principal=na_osobe,
    )
    return create_app(
        config.model_copy(
            update={"security": config.security.model_copy(update={"diagnostics": diagnostyka})}
        ),
        config_dir=config_dir,
        audit=AuditLog(),
        doctor_probe=sonda,
    )


def test_ponad_limit_dostaje_429(repo_config_dir: Path) -> None:
    """Sedno: `diagnostics:read` nie może być dźwignią do generowania ruchu.

    Żądanie jest tanie dla wywołującego (jedno GET), a kosztowne dla instalacji — otwiera
    połączenie do KAŻDEGO endpointu z konfiguracji.
    """
    sonda = _SondaLiczaca()
    client = TestClient(_app_z_limitem(repo_config_dir, 2, sonda))

    kody = [client.get("/api/doctor").status_code for _ in range(4)]

    assert kody[:2] == [200, 200], kody
    assert kody[2:] == [429, 429], kody


def test_zadanie_ponad_limit_NIE_generuje_ruchu(repo_config_dir: Path) -> None:
    """Odmowa musi nastąpić PRZED sondowaniem — inaczej limit chroni tylko odpowiedź.

    To jest właściwa treść tej poprawki: 429 zwrócone po odpytaniu silników nie zmniejszyłoby
    ani jednego pakietu, a więc niczego by nie zabezpieczało.
    """
    sonda = _SondaLiczaca()
    client = TestClient(_app_z_limitem(repo_config_dir, 1, sonda))

    client.get("/api/doctor")
    po_pierwszym = len(sonda.zapytania)
    assert po_pierwszym > 0, "test byłby pusty, gdyby pierwsze żądanie nic nie sondowało"

    assert client.get("/api/doctor").status_code == 429
    assert len(sonda.zapytania) == po_pierwszym, "żądanie ponad limit odpytało silniki"


def test_komunikat_429_wskazuje_MIEJSCE_w_konfiguracji(repo_config_dir: Path) -> None:
    """Operator ma wiedzieć, gdzie ten limit zmienić — inaczej zostaje z samym „za dużo"."""
    client = TestClient(_app_z_limitem(repo_config_dir, 1, _SondaLiczaca()))
    client.get("/api/doctor")

    detail = client.get("/api/doctor").json()["detail"]

    assert "security.diagnostics" in detail
    assert "odpytuje silniki" in detail


def test_limit_mozna_wylaczyc_swiadomie(repo_config_dir: Path) -> None:
    """Nośność: `None` to REZYGNACJA z zabezpieczenia, ale musi działać jak obiecano."""
    client = TestClient(_app_z_limitem(repo_config_dir, None, _SondaLiczaca()))

    kody = [client.get("/api/doctor").status_code for _ in range(8)]

    assert kody == [200] * 8, kody


def test_nadpisanie_konfiguracji_w_runtime_NIE_zeruje_limitu(repo_config_dir: Path) -> None:
    """Ogranicznik budowany raz, ze startu — inaczej `POST /api/config/runtime` byłby obejściem.

    Przebudowa ogranicznika przy każdym nadpisaniu konfiguracji zerowałaby kubełek, więc
    wystarczyłoby przeplatać diagnozę pustymi nadpisaniami, żeby limit przestał istnieć.
    """
    sonda = _SondaLiczaca()
    client = TestClient(_app_z_limitem(repo_config_dir, 1, sonda))

    assert client.get("/api/doctor").status_code == 200
    assert client.post("/api/config/runtime", json={"overrides": {}}).status_code == 200

    assert client.get("/api/doctor").status_code == 429, "nadpisanie configu zresetowało limit"
