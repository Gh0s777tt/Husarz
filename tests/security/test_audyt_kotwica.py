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


# ------------------------------------------------ klucz HMAC: domknięcie przekucia łańcucha


class _Sekrety:
    """Dostawca sekretów o umówionej referencji."""

    def __init__(self, klucz: str | None = "tajny-klucz-audytu") -> None:
        self._klucz = klucz

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz dla `env:KLUCZ`, w przeciwnym razie None."""
        return self._klucz if ref == "env:KLUCZ" else None


def _konfiguracja(sciezka: Path, *, ref: str | None = "env:KLUCZ"):  # noqa: ANN202
    from husarz.config.schema import AuditConfig, SecurityConfig

    return SecurityConfig(audit=AuditConfig(enabled=True, path=sciezka, hmac_key_ref=ref))


def test_przekucie_calego_lancucha_bez_klucza_jest_WYKRYWANE(tmp_path: Path) -> None:
    """Luka, której kotwica z założenia nie zamyka.

    Kto ma prawo zapisu do pliku, może przeliczyć goły SHA-256 od nowa i podmienić CAŁĄ
    historię — łańcuch będzie wtedy wewnętrznie spójny, a kotwicę wystarczy nadpisać.
    Klucz HMAC trzymany POZA systemem plików czyni to niewykonalne.
    """
    from husarz.security.audit import build_audit_log
    from husarz.security.errors import AuditError

    sciezka = tmp_path / "audit.log"
    cfg = _konfiguracja(sciezka)
    log = build_audit_log(cfg, secrets=_Sekrety())
    for i in range(3):
        log.record("api", f"akcja-{i}", {"i": i})
    assert log.verify() is True, "założenie testu"

    # Napastnik BEZ klucza przepisuje dziennik i kotwicę od zera.
    sciezka.unlink()
    (tmp_path / "audit.log.kotwica").unlink()
    podrobiony = AuditLog(path=sciezka, anchor_path=tmp_path / "audit.log.kotwica")
    for i in range(3):
        podrobiony.record("api", f"PODMIENIONE-{i}", {"i": i})

    assert (
        AuditLog.load(sciezka).verify() is True
    ), "podrobiony łańcuch JEST wewnętrznie spójny — na tym polega ta droga ataku"
    with pytest.raises(AuditError, match="NIE weryfikuje się kluczami"):
        build_audit_log(cfg, secrets=_Sekrety())


def test_brak_dostawcy_sekretow_to_ODMOWA_a_nie_cicha_praca_bez_klucza(tmp_path: Path) -> None:
    """Cicha praca bez klucza byłaby najgorszym wyjściem.

    Operator, który skonfigurował `hmac_key_ref`, ma prawo sądzić, że dziennik jest chroniony.
    Milczące przejście w tryb bez klucza zamieniłoby zabezpieczenie w jego pozór.
    """
    from husarz.security.audit import build_audit_log
    from husarz.security.errors import AuditError

    with pytest.raises(AuditError, match="nie przekazano dostawcy"):
        build_audit_log(_konfiguracja(tmp_path / "audit.log"), secrets=None)


def test_nierozwiazywalna_referencja_to_ODMOWA(tmp_path: Path) -> None:
    """Ta sama zasada: brak materiału pod referencją nie może degradować cicho."""
    from husarz.security.audit import build_audit_log
    from husarz.security.errors import AuditError

    with pytest.raises(AuditError, match="Nie udało się rozwiązać klucza"):
        build_audit_log(_konfiguracja(tmp_path / "audit.log"), secrets=_Sekrety(klucz=None))


def test_bez_klucza_uszkodzony_lancuch_BLOKUJE_start(tmp_path: Path) -> None:
    """SPROSTOWANIE wcześniejszej decyzji tego pliku.

    Do Etapu 18 stało tu twierdzenie odwrotne: że bez klucza HMAC dziennik pozostaje
    doradczy, bo „skonfigurowanie klucza jest deklaracją, że integralność jest blokująca",
    a zmiana byłaby osobną decyzją. Decyzja została podjęta i wypadła inaczej, bo tamto
    rozumowanie miało dziurę: uszkodzenie było wprawdzie WIDOCZNE jako `verified: false`
    w `GET /api/audit`, ale wyłącznie dla kogoś, kto sam o to zapytał. W praktyce znaczyło
    to, że instalacja bez klucza pracowała dalej na dzienniku, który nic już nie dowodzi.

    Nowa wartość domyślna to `integrity: blocking`. Czego ona NIE daje, mówimy wprost
    w komunikacie błędu: bez klucza HMAC kontrola wykrywa uszkodzenie, ale nie odróżnia
    go od świadomej podmiany.
    """
    from husarz.security.audit import build_audit_log
    from husarz.security.errors import AuditError

    sciezka = tmp_path / "audit.log"
    log = build_audit_log(_konfiguracja(sciezka, ref=None))
    log.record("api", "a", {})
    linie = sciezka.read_text(encoding="utf-8").splitlines()
    sciezka.write_text(linie[0].replace('"a"', '"PODMIENIONE"') + "\n", encoding="utf-8")

    with pytest.raises(AuditError, match="nie odróżnia"):
        build_audit_log(_konfiguracja(sciezka, ref=None))


def test_klucz_z_magazynu_husarza_jest_ZABRONIONY() -> None:
    """Zamknięty krąg: klucz integralności audytu nie może pochodzić z magazynu,
    który należy do systemu pilnowanego przez ten audyt."""
    from husarz.config.schema import AuditConfig

    with pytest.raises(ValueError, match="ZEWNĘTRZNEGO"):
        AuditConfig(hmac_key_ref="husarz:klucz-audytu")

    assert AuditConfig(hmac_key_ref="env:KLUCZ").hmac_key_ref == "env:KLUCZ"


def test_material_klucza_w_configu_jest_ZABRONIONY() -> None:
    """Niezmiennik projektu: konfiguracja nie zawiera materiału, tylko referencje."""
    from husarz.config.schema import AuditConfig

    with pytest.raises(ValueError, match="referencją do sekretu"):
        AuditConfig(hmac_key_ref="to nie jest referencja")
