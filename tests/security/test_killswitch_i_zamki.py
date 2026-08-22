"""Kill-switch magazynu, domknięcie zamków i echo w komunikatach — Etap 17g.

Pięć ostatnich zgłoszeń z drugiego przeglądu, odciętych przez limit weryfikacji. Cztery
potwierdziłem uruchomieniem, jedno (bezwzględne ścieżki operatora w odpowiedziach) **się nie
potwierdziło** — pierwszy taki przypadek w tej serii przeglądów.

Opis: `docs/BEZPIECZENSTWO.md`, sekcja „Etap 17g".
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

_BAZA = {"provider": "github", "api_base": "https://api.github.com"}


class _DictSecrets:
    """Dostawca klucza głównego magazynu."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz główny dla umówionej referencji."""
        return "klucz-glowny-testowy" if ref == "env:KLUCZ" else None


# --------------------------------------------- 1. kill-switch: wyłączenie odcina ODCZYT


def test_wylaczony_magazyn_nie_rozwiazuje_istniejacych_referencji(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`enabled: false` to KILL-SWITCH, nie tylko zakaz zapisu.

    Przed poprawką wyłączenie zamykało wyłącznie ZAPIS: dotychczasowe tokeny nadal się
    rozwiązywały i nadal uwierzytelniały operacje Gita. Operator wyłączający magazyn — zwykle
    w reakcji na incydent — oczekuje, że przestanie on wydawać materiał.
    """
    from husarz.launcher import cli

    magazyn = build_secret_store(
        path=tmp_path / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )
    magazyn.put("git/gh", "ghp_ISTNIEJACY")
    monkeypatch.setattr(cli, "_SEKRETY", magazyn)

    dostepny = cli._SchemeSecrets(magazyn_dostepny=True)  # noqa: SLF001
    odciety = cli._SchemeSecrets(magazyn_dostepny=False)  # noqa: SLF001

    assert dostepny.resolve("husarz:git/gh") == "ghp_ISTNIEJACY"
    assert odciety.resolve("husarz:git/gh") is None
    # Nośność: obie instancje patrzą na TEN SAM magazyn, więc różnica bierze się z bramki.
    assert magazyn.resolve("husarz:git/gh") == "ghp_ISTNIEJACY"


def test_killswitch_nie_dotyka_pozostalych_schematow(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nośność: bramka obejmuje WYŁĄCZNIE `husarz:` — reszta źródeł działa niezależnie."""
    from husarz.launcher import cli

    monkeypatch.setenv("HUSARZ_ZWYKLY", "wartosc-ze-srodowiska")
    monkeypatch.setattr(cli, "_SEKRETY", None)

    odciety = cli._SchemeSecrets(magazyn_dostepny=False)  # noqa: SLF001

    assert odciety.resolve("env:HUSARZ_ZWYKLY") == "wartosc-ze-srodowiska"


# ------------------------------------------- 2. bramka sprawdzana POD zamkiem


class _WolnaTrwalosc(FileGitConnectionStore):
    """Magazyn połączeń z opóźnioną odpowiedzią o trwałości.

    Sprawdzenie trwałości dzieje się PRZED wejściem pod zamek, więc opóźnienie tutaj otwiera
    dokładnie to okno, w którym magazyn może zostać wyłączony w trakcie obsługi żądania.
    """

    def __init__(self, path: Path, opoznienie: float = 0.4) -> None:
        super().__init__(path)
        self._opoznienie = opoznienie

    @property
    def persistent(self) -> bool:
        """Zwraca ``True`` po sztucznej pauzie."""
        time.sleep(self._opoznienie)
        return True


def _srodowisko(
    repo_config_dir: Path, *, wolna_trwalosc: bool = False
) -> tuple[TestClient, EncryptedFileSecretStore, Path]:
    katalog = Path(tempfile.mkdtemp())
    magazyn = build_secret_store(
        path=katalog / "store.json", key_ref="env:KLUCZ", secrets=_DictSecrets()
    )
    store = (
        _WolnaTrwalosc(katalog / "conn.json")
        if wolna_trwalosc
        else FileGitConnectionStore(katalog / "conn.json")
    )
    app = create_app(
        load_config(
            repo_config_dir,
            runtime_overrides={
                "security": {
                    "secret_store": {
                        "enabled": True,
                        "key_ref": "env:KLUCZ",
                        "path": str(katalog / "store.json"),
                    }
                }
            },
        ),
        config_dir=repo_config_dir,
        audit=AuditLog(path=katalog / "audit.jsonl"),
        git_service=GitService(store=store),
        secret_store=magazyn,
    )
    return TestClient(app), magazyn, katalog


def test_wylaczenie_w_trakcie_zadania_zatrzymuje_zapis(repo_config_dir: Path) -> None:
    """Kontrola bezpieczeństwa nie może mieć okna „prawie zamknięte".

    Bramka sprawdzana wyłącznie PRZED zamkiem przepuszczała żądanie, które weszło tuż przed
    wyłączeniem magazynu — token lądował na dysku JUŻ PO tym, jak operator go wyłączył.
    """
    client, magazyn, _ = _srodowisko(repo_config_dir, wolna_trwalosc=True)
    wystartowal = threading.Event()
    wyniki: dict[str, Any] = {}

    def kreator() -> None:
        wystartowal.set()
        wyniki["wizard"] = client.post(
            "/api/git/connections/wizard", json={**_BAZA, "name": "gh", "token": "ghp_W_LOCIE"}
        )

    def wylacz() -> None:
        wystartowal.wait(timeout=5)
        time.sleep(0.15)  # wejdź w okno otwarte przez wolne sprawdzenie trwałości
        wyniki["off"] = client.post(
            "/api/config/runtime",
            json={"overrides": {"security": {"secret_store": {"enabled": False}}}},
        )

    with ThreadPoolExecutor(max_workers=2) as pula:
        for f in [pula.submit(kreator), pula.submit(wylacz)]:
            f.result(timeout=20)

    assert wyniki["off"].json()["ok"] is True
    assert wyniki["wizard"].status_code == 409, wyniki["wizard"].text
    assert magazyn.names() == [], "token zapisano PO wyłączeniu magazynu"


# ------------------------------------------- 3. echo wartości `ca_bundle`


def test_komunikat_o_bledzie_ca_nie_powtarza_wartosci(repo_config_dir: Path) -> None:
    """Druga droga echa obok bramki 422 — odpowiedź 400 odsyłała wartość pola.

    Wartość `ca_bundle` to ścieżka, nie sekret, ale ten sam kanał obsługuje pole, w które
    operator może omyłkowo wkleić cokolwiek. Niezmiennik jest jeden: API nie odsyła tego,
    co dostało.
    """
    client, _, _ = _srodowisko(repo_config_dir)
    wartosc = "/sciezka/ktorej/nie/ma/ghp_UDAJACY_TOKEN.pem"

    odp = client.post(
        "/api/git/connections/wizard",
        json={**_BAZA, "name": "ca", "token": "ghp_X", "ca_bundle": wartosc},
    )

    assert odp.status_code == 400
    assert wartosc not in odp.text
    assert "ghp_UDAJACY_TOKEN" not in odp.text
    # Nośność: komunikat musi nadal wskazywać POLE i mówić, co jest nie tak.
    assert "ca_bundle" in odp.text
    assert "PEM" in odp.text


# ------------------------------------------- 4. obie drogi dodawania pod zamkiem


def test_rownolegle_dodawanie_przez_referencje_nie_gubi_polaczen(
    repo_config_dir: Path,
) -> None:
    """Równoległe dodawanie nie gubi połączeń.

    **Uczciwie o zasięgu tego testu.** Przechodzi on ZARÓWNO z objęciem endpointu zamkiem,
    jak i bez — magazyn połączeń ma własną synchronizację, więc równoległe dodania o różnych
    nazwach działały i wcześniej. Test sprawdza więc brak utraty aktualizacji, a NIE poprawkę
    z Etapu 17g. Nośności dla niej brak i jest to zapisane świadomie: groźne okno to dwie
    sąsiednie instrukcje w `DELETE` (lista połączeń → usunięcie sekretu), którego nie da się
    otworzyć deterministycznie bez wstrzyknięcia pauzy w kod produkcyjny. Struktury pilnuje
    osobny test niżej.
    """
    client, _, _ = _srodowisko(repo_config_dir)
    nazwy = [f"conn-{i}" for i in range(8)]

    with ThreadPoolExecutor(max_workers=8) as pula:
        wyniki = list(
            pula.map(
                lambda n: client.post(
                    "/api/git/connections", json={**_BAZA, "name": n, "token_ref": "env:GH"}
                ),
                nazwy,
            )
        )

    assert [r.status_code for r in wyniki] == [200] * len(nazwy)
    assert sorted(c["name"] for c in client.get("/api/git/connections").json()) == sorted(nazwy)


def test_kolizja_nazwy_przez_referencje_nadal_daje_409(repo_config_dir: Path) -> None:
    """Nośność: objęcie zamkiem nie może zmienić zachowania przy kolizji."""
    client, _, _ = _srodowisko(repo_config_dir)
    ciało = {**_BAZA, "name": "gh", "token_ref": "env:GH"}

    assert client.post("/api/git/connections", json=ciało).status_code == 200
    assert client.post("/api/git/connections", json=ciało).status_code == 409


# ------------------------------------------- 5. konsola: błędy walidacji


def test_konsola_nie_sklejaja_tablicy_bledow_ze_stringiem() -> None:
    """Regresja UI: `"Błąd: " + detail` dawało `[object Object]` dla błędów walidacji.

    Odpowiedź 422 niesie TABLICĘ obiektów, więc konkatenacja ze stringiem gubiła całą treść —
    akurat tam, gdzie użytkownik pomylił się w formularzu i najbardziej potrzebuje komunikatu.
    Sprawdzamy ŹRÓDŁO konsoli, bo to jedyny sposób bez uruchamiania przeglądarki; test chroni
    przed ponownym wprowadzeniem wzorca.
    """
    zrodlo = Path("src/husarz/api/static/console.html").read_text(encoding="utf-8")

    assert "function opisBledu" in zrodlo, "brak pomocnika formatującego błędy"
    assert '"Błąd: " + (d.detail' not in zrodlo, "wrócił wzorzec sklejający tablicę ze stringiem"
    assert zrodlo.count("opisBledu(d)") >= 2, "nie wszystkie miejsca korzystają z pomocnika"


def test_obie_drogi_dodawania_sa_pod_zamkiem() -> None:
    """Kontrola STRUKTURALNA — świadomy wyjątek od zasady „sprawdzaj skutek".

    Wzajemne wykluczanie da się sprawdzić przez skutek tylko wtedy, gdy okno wyścigu da się
    otworzyć. Tutaj jest ono dwiema sąsiednimi instrukcjami i wymagałoby pauzy wstrzykniętej
    w kod produkcyjny — czyli testu, który zmienia to, co bada. Sprawdzamy więc ŹRÓDŁO:
    obie drogi dodawania muszą wołać `svc.add` pod zamkiem. To słabszy dowód niż odtworzenie
    awarii i tak go traktujemy; chroni przed usunięciem zamka, nie dowodzi jego poprawności.
    """
    import re

    zrodlo = Path("src/husarz/api/app.py").read_text(encoding="utf-8")
    wywolania = [m.start() for m in re.finditer(r"\bsvc\.add\(conn\)", zrodlo)]

    assert len(wywolania) == 2, f"oczekiwano dwóch dróg dodawania, jest {len(wywolania)}"
    for pozycja in wywolania:
        poprzedzajace = zrodlo[:pozycja]
        ostatni_zamek = poprzedzajace.rfind("with _mutex_polaczen:")
        ostatnia_definicja = poprzedzajace.rfind("    def ")
        assert (
            ostatni_zamek > ostatnia_definicja
        ), "svc.add(conn) poza zamkiem — jedna z dróg dodawania nie jest chroniona"
