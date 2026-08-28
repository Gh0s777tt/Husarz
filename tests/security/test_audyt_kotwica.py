"""Odcięcie OGONA dziennika audytu — luka, której łańcuch skrótów nie zamykał.

**Skąd ten plik.** Przy przeszukaniu pól konfiguracji (Etap 17m) padło pytanie o pole
`audit.immutable`. Przy okazji wyszło coś poważniejszego od samego martwego przełącznika:
łańcuch skrótów wykrywa EDYCJĘ wpisu, ale nie wykrywa USUNIĘCIA końcówki. Odtworzone:
dziennik z pięcioma wpisami po usunięciu dwóch ostatnich nadal przechodził `verify()`.

Dla dziennika opisywanego w dokumentacji jako „niemodyfikowalny" to luka istotna, bo
najłatwiejszym sposobem zatarcia śladu jest właśnie usunięcie końcówki — nie edycja w środku.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from husarz.security.audit import AuditLog

pytestmark = pytest.mark.security


def _dziennik(tmp_path: Path, wpisow: int = 5) -> tuple[Path, Path]:
    """Tworzy dziennik z kotwicą i zwraca obie ścieżki."""
    plik = tmp_path / "audit.log"
    kotwica = tmp_path / "audit.log.kotwica"
    log = AuditLog(path=plik, anchor_path=kotwica)
    for i in range(wpisow):
        log.record("test", f"akcja-{i}", {"i": i})
    return plik, kotwica


def test_odciecie_ogona_jest_WYKRYWANE(tmp_path: Path) -> None:
    """Regresja wprost: to jest zachowanie, dla którego kotwica powstała."""
    plik, kotwica = _dziennik(tmp_path)
    linie = plik.read_text(encoding="utf-8").splitlines()
    assert AuditLog.load(plik, anchor_path=kotwica).verify() is True, "założenie testu"

    plik.write_text("\n".join(linie[:-2]) + "\n", encoding="utf-8")

    assert AuditLog.load(plik, anchor_path=kotwica).verify() is False
    # Bez tej asercji test przeszedłby także wtedy, gdyby `verify()` zawsze zwracało False.
    assert (
        AuditLog.load(plik).verify() is True
    ), "bez kotwicy odcięcie POZOSTAJE niewykrywalne — to jest dokładnie ta luka"


def test_przepisanie_koncowki_o_TEJ_SAMEJ_dlugosci_jest_wykrywane(tmp_path: Path) -> None:
    """Sama liczba wpisów by nie wystarczyła.

    Napastnik może usunąć końcówkę i dopisać własną, tej samej długości. Dlatego kotwica
    trzyma SKRÓT wpisu na swojej pozycji, nie tylko licznik.
    """
    plik, kotwica = _dziennik(tmp_path)
    linie = plik.read_text(encoding="utf-8").splitlines()

    # Nowy dziennik od zera, o tej samej liczbie wpisów, ale innej historii.
    plik.unlink()
    podrobiony = AuditLog(path=plik)
    for i in range(len(linie)):
        podrobiony.record("test", f"podmienione-{i}", {"i": i})

    assert AuditLog.load(plik).verify() is True, "podrobiony łańcuch jest wewnętrznie spójny"
    assert AuditLog.load(plik, anchor_path=kotwica).verify() is False, "kotwica tego nie złapała"


def test_dziennik_WYPRZEDZAJACY_kotwice_jest_w_porzadku(tmp_path: Path) -> None:
    """Przerwanie między zapisem wpisu a zapisem kotwicy NIE jest manipulacją.

    Kolejność jest celowa: wpis najpierw, kotwica potem. Odwrotna zostawiałaby kotwicę
    wskazującą na wpis, którego nie ma — czyli fałszywy alarm manipulacji po zwykłym
    zaniku zasilania. Ten test pilnuje, że wybraliśmy właściwą stronę.
    """
    plik, kotwica = _dziennik(tmp_path, wpisow=3)

    # Symulacja awarii: dopisujemy wpisy do dziennika, kotwicy NIE aktualizujemy.
    log = AuditLog(path=plik)
    log._entries.extend(AuditLog.load(plik).entries)
    log._last_hash = log._entries[-1].entry_hash
    log.record("test", "po-awarii", {})

    assert AuditLog.load(plik, anchor_path=kotwica).verify() is True


def test_uszkodzona_kotwica_NIE_uniewaznia_dziennika(tmp_path: Path) -> None:
    """Fałszywy alarm manipulacji po uszkodzeniu pliku POMOCNICZEGO byłby gorszy niż jego brak.

    Operator, który raz zobaczy „łańcuch USZKODZONY" bez powodu, przestanie temu komunikatowi
    wierzyć — a wtedy nie zareaguje, gdy komunikat będzie prawdziwy.
    """
    plik, kotwica = _dziennik(tmp_path)
    kotwica.write_text("to nie jest JSON", encoding="utf-8")

    assert AuditLog.load(plik, anchor_path=kotwica).verify() is True


def test_kotwica_jest_zapisywana_atomowo_i_niesie_oba_pola(tmp_path: Path) -> None:
    """Kotwica widziana w stanie połowicznym byłaby gorsza niż jej brak."""
    plik, kotwica = _dziennik(tmp_path, wpisow=2)

    dane = json.loads(kotwica.read_text(encoding="utf-8"))

    assert dane["wpisow"] == 2
    assert dane["skrot"] == AuditLog.load(plik).entries[-1].entry_hash
    assert not list(tmp_path.glob("*.tmp")), "plik tymczasowy nie został posprzątany"


def test_edycja_w_miejscu_nadal_jest_wykrywana(tmp_path: Path) -> None:
    """Nośność: kotwica nie może zastąpić łańcucha ani go osłabić."""
    plik, kotwica = _dziennik(tmp_path)
    linie = plik.read_text(encoding="utf-8").splitlines()
    linie[1] = linie[1].replace("akcja-1", "akcja-PODMIENIONA")
    plik.write_text("\n".join(linie) + "\n", encoding="utf-8")

    assert AuditLog.load(plik, anchor_path=kotwica).verify() is False
    assert AuditLog.load(plik).verify() is False, "sam łańcuch też ma to łapać"


def test_build_audit_log_zaklada_kotwice_obok_dziennika(tmp_path: Path) -> None:
    """Produkcyjna fabryka musi to włączać sama — inaczej ochrona istnieje tylko w testach."""
    from husarz.config.schema import AuditConfig, SecurityConfig
    from husarz.security.audit import build_audit_log

    sciezka = tmp_path / "audyt" / "audit.log"
    log = build_audit_log(SecurityConfig(audit=AuditConfig(enabled=True, path=sciezka)))
    log.record("test", "a", {})

    assert log.anchor_path is not None
    assert log.anchor_path.is_file(), "fabryka nie założyła kotwicy"
    assert log.anchor_path.parent == sciezka.parent
