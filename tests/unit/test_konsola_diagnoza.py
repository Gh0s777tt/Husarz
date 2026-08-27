"""Panel „Diagnoza" w konsoli WWW — kontrola ŹRÓDŁA (świadomie słabsza od testu skutku).

**Czego te testy NIE dowodzą.** Nie uruchamiają przeglądarki, więc nie sprawdzają, że panel
faktycznie się rysuje ani że kliknięcie zakładki cokolwiek pobiera. Dowodzą wyłącznie, że
w źródle konsoli są elementy i wiązania, bez których panel działać NIE MOŻE, oraz że każda
wartość z odpowiedzi API przechodzi przez escapowanie. Skutek zweryfikowano osobno,
uruchamiając aplikację i otwierając konsolę — wynik zapisany w `docs/BEZPIECZENSTWO.md`.

Ta luka jest zapisana wprost, a nie przemilczana: zapisana luka jest uczciwa, pozorne
pokrycie jest szkodliwe.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ZRODLO = Path("src/husarz/api/static/console.html").read_text(encoding="utf-8")


def _cialo_funkcji(nazwa: str) -> str:
    """Wycina treść funkcji od jej nagłówka do znacznika końca (heurystyka na potrzeby testu)."""
    poczatek = _ZRODLO.index(f"function {nazwa}(")
    return _ZRODLO[poczatek : _ZRODLO.index("\n    }", poczatek)]


def test_zakladka_i_sekcja_istnieja() -> None:
    """Bez przycisku w nawigacji panel jest nieosiągalny, choćby kod działał."""
    assert '<button data-tab="doctor">' in _ZRODLO
    assert 'id="tab-doctor"' in _ZRODLO
    assert 'id="doctor-out"' in _ZRODLO


def test_zakladka_jest_wpieta_w_przelacznik_i_w_przycisk_odswiezania() -> None:
    """Dwie drogi do tej samej funkcji: wejście w zakładkę i „Sprawdź ponownie"."""
    assert 'if (tab === "doctor") loadDoctor();' in _ZRODLO
    assert '$("doctor-refresh").onclick = loadDoctor;' in _ZRODLO


def test_kazda_wartosc_z_odpowiedzi_przechodzi_przez_escapowanie() -> None:
    """Ustalenia niosą nazwy modeli i endpointy Z KONFIGURACJI — czyli tekst spoza kodu.

    Rola z `config:write` może wpisać jako nazwę modelu `<img onerror=...>`, a diagnoza
    wypisze ją operatorowi. Panel escapuje wszystko; ten test pilnuje, żeby przy dopisywaniu
    kolumny nikt o tym nie zapomniał.

    Uwaga dla poprawiającego: skanujemy SUROWE źródło, więc wstawka szablonowa zapisana
    w komentarzu też zostanie zgłoszona. To świadomy kompromis — parsowanie komentarzy JS
    w teście kosztowałoby więcej, niż przeredagowanie komentarza.
    """
    cialo = _cialo_funkcji("loadDoctor")
    wstawki = re.findall(r"\$\{([^}]*)\}", cialo)
    assert wstawki, "test byłby pusty, gdyby panel nic nie wstawiał"

    # Jedyne dopuszczone wstawki poza `esc(...)`: trzy nazwy przypisane w tej samej funkcji
    # z LITERAŁÓW w kodzie (klasa CSS i znak stanu). Lista jest krótka i jawna celowo —
    # rosnąca lista wyjątków oznaczałaby, że kontrola przestaje cokolwiek znaczyć.
    dozwolone_lokalne = {"klasa", "znak", "klasaStanu"}
    surowe = [w for w in wstawki if "esc(" not in w and w.strip() not in dozwolone_lokalne]
    assert not surowe, f"nieescapowane wstawki w panelu diagnozy: {surowe}"


def test_panel_NIE_liczy_oceny_sam() -> None:
    """Obietnica „jedno źródło prawdy": ocenę liczy API, panel ją wyświetla.

    Gdyby konsola liczyła problemy po swojemu (np. filtrując `findings`), mogłaby ocenić
    tę samą instalację inaczej niż `husarz doctor` — a operator nie wiedziałby, któremu
    nośnikowi wierzyć.
    """
    cialo = _cialo_funkcji("loadDoctor")

    assert "d.blocking" in cialo and "d.warnings" in cialo and "d.unknown" in cialo
    assert ".filter(" not in cialo, "panel filtruje ustalenia zamiast użyć liczników z API"


def test_odmowa_dostepu_NIE_wyglada_jak_brak_problemow() -> None:
    """403 dla roli bez `diagnostics:read` musi być widoczny jako błąd.

    Pusta tabela w miejscu odmowy czyta się jak „wszystko w porządku" — to ta sama klasa
    pomyłki co zaokrąglanie „nie dało się sprawdzić" do „OK".
    """
    cialo = _cialo_funkcji("loadDoctor")

    assert "!r.ok" in cialo, "brak sprawdzenia kodu odpowiedzi"
    assert "opisBledu(d)" in cialo
    assert "class='err'" in cialo


def test_blad_czatu_kieruje_do_diagnozy() -> None:
    """Gołe „Backend modelu zawiódł" to komunikat, dla którego diagnoza powstała."""
    assert "function bladCzatu" in _ZRODLO
    assert (
        _ZRODLO.count("out.innerHTML = bladCzatu(d); wireDiagHint(out);") == 2
    ), "obie drogi (czat i orkiestracja) mają prowadzić do diagnozy"
    assert 'nav button[data-tab="doctor"]' in _ZRODLO


def test_nieznany_stan_ma_wlasny_kolor() -> None:
    """NIEZNANY nie może dzielić koloru z OK ani z problemem — to trzeci stan."""
    assert "--warn:" in _ZRODLO
    assert ".warn { color: var(--warn); }" in _ZRODLO
    assert '"nieznany": ["warn"' in _ZRODLO


def test_indeksowanie_mapy_stanow_nie_siega_do_prototypu() -> None:
    """Stan o nazwie `constructor` wywróciłby destrukturyzację i usunął CAŁĄ tabelę."""
    assert "Object.hasOwn(DOCTOR_ZNAKI, klucz)" in _ZRODLO


def test_konsola_i_CLI_odrozniaja_TE_SAME_przypadki() -> None:
    """Niezmiennik „jedno źródło prawdy" dotyczy także ODCZYTU, nie tylko liczb.

    Gdyby konsola i `husarz doctor` grupowały ustalenia inaczej — np. terminal odróżniał
    problem blokujący od ostrzeżenia, a tabela nie — operator dostałby dwie różne oceny tej
    samej instalacji i nie wiedziałby, której wierzyć. To ten sam błąd, przed którym broni
    liczenie podsumowania po stronie API, tylko przeniesiony o warstwę wyżej.

    Sprawdzamy ROZRÓŻNIALNOŚĆ, nie identyczność znaków: terminal ma cztery znaki ASCII,
    tabela — kolor i glif. Wspólne ma być to, KTÓRE przypadki wyglądają inaczej.
    """
    import re

    from husarz.launcher.doctor import Stan, Ustalenie, Waga, znacznik

    przypadki = {
        "ok": Ustalenie(id="x", stan=Stan.OK, waga=Waga.INFORMACJA, opis="."),
        "problem/blokujaca": Ustalenie(id="x", stan=Stan.PROBLEM, waga=Waga.BLOKUJACA, opis="."),
        "problem/ostrzezenie": Ustalenie(
            id="x", stan=Stan.PROBLEM, waga=Waga.OSTRZEZENIE, opis="."
        ),
        "nieznany": Ustalenie(id="x", stan=Stan.NIEZNANY, waga=Waga.BLOKUJACA, opis="."),
    }

    # Terminal: każdy z czterech przypadków ma WŁASNY znacznik.
    znaczniki = {klucz: znacznik(u) for klucz, u in przypadki.items()}
    assert len(set(znaczniki.values())) == 4, znaczniki

    # Konsola: mapa musi mieć wpis dla każdego z tych kluczy, a blokujący nie może dzielić
    # wyglądu z ostrzeżeniem (to jest cała treść tej poprawki).
    mapa = re.search(r"const DOCTOR_ZNAKI = \{(.*?)\n    \};", _ZRODLO, re.S)
    assert mapa, "nie znaleziono mapy znaków w konsoli"
    tresc = mapa.group(1)
    for klucz in przypadki:
        assert f'"{klucz}"' in tresc, f"konsola nie zna przypadku {klucz}"
    assert tresc.count('["err"') == 1, "kolor błędu ma być zarezerwowany dla problemu blokującego"


def test_klucz_konsoli_laczy_stan_z_waga() -> None:
    """Sam stan nie wystarczy — to była przyczyna nierozróżnialności."""
    assert 'f.state === "problem" ? `problem/${f.severity}`' in _ZRODLO
