"""Odporność dziennika audytu na SKURCZENIE pliku i stany, w których nie wiadomo, co w nim jest.

**Skąd ten plik.** Z przeglądu adwersaryjnego Etapu 18, który wykrył REGRESJĘ wprowadzoną
przez samą naprawę wyścigu międzyprocesowego. `_odswiez_z_pliku` przyjmowało każdą zmianę
rozmiaru pliku jako „dopisał ktoś inny" — a plik wyłącznie dopisujący nie może się skurczyć.
Skutek odwracał sens kotwicy z Etapu 17n, co odtworzono pomiarem:

* dziennik ma 5 wpisów, kotwica mówi ``{"wpisow": 5}``,
* napastnik obcina PLIK do 2 wpisów, kotwicy nie ruszając — ``verify()`` zwraca ``False``,
* ofiara wykonuje JEDEN zwykły wpis (np. logowanie),
* ten wpis wczytuje obcięty plik jako prawdę i przepisuje kotwicę na ``{"wpisow": 3}``,
* od tej chwili ``verify()`` zwraca ``True``, a trzy wpisy zniknęły bez śladu.

Ofiara własnymi rękami zacierała jedyny dowód. Napastnik nie był przy tym potrzebny: to samo
robiła zwykła rotacja pliku (`mv audit.log audit.log.1`), czyli dokładnie ta klasa zdarzeń,
dla której kotwica powstała.

Naprawa jest DWUCZĘŚCIOWA i testy pilnują obu części osobno, bo każda broni sama:
skurczenie pliku jest twardą odmową, a kotwica ma zapadkę i nigdy nie idzie w dół.
Zapadka nie jest nadmiarowa — zamyka wszystkie przyszłe ścieżki, które zresetują `_entries`,
a nie tylko tę jedną, którą znaleziono.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from husarz.config.schema import AuditConfig, AuditIntegrity, SecurityConfig
from husarz.core.filelock import FileLockError, blokada_pliku
from husarz.security.audit import AuditLog, build_audit_log
from husarz.security.errors import AuditError

pytestmark = pytest.mark.security

KLUCZ = b"klucz-testowy-audytu"


def _dziennik(sciezka: Path, wpisow: int = 5) -> AuditLog:
    """Dziennik z kotwicą i podaną liczbą wpisów."""
    log = AuditLog(
        path=sciezka, hmac_key=KLUCZ, anchor_path=sciezka.with_name(sciezka.name + ".kotwica")
    )
    for i in range(wpisow):
        log.record("api", f"zdarzenie-{i}", {"i": i})
    return log


def _obetnij(sciezka: Path, do_wpisow: int) -> None:
    """Zostawia w pliku tylko pierwsze ``do_wpisow`` linii."""
    linie = sciezka.read_text(encoding="utf-8").strip().splitlines()
    sciezka.write_text("\n".join(linie[:do_wpisow]) + "\n", encoding="utf-8")


def _kotwica(sciezka: Path) -> dict[str, object]:
    """Odczytuje kotwicę jako słownik."""
    plik = sciezka.with_name(sciezka.name + ".kotwica")
    return dict(json.loads(plik.read_text(encoding="utf-8")))


# --------------------------------------------------------------------------------------
# Regresja krytyczna: pranie odcięcia ogona
# --------------------------------------------------------------------------------------


def test_obciecie_ogona_NIE_jest_prane_przez_kolejny_wpis(tmp_path: Path) -> None:
    """Regresja wprost — to zachowanie, dla którego ten plik powstał."""
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka)
    assert _kotwica(sciezka)["wpisow"] == 5, "założenie testu"
    _obetnij(sciezka, 2)

    with pytest.raises(AuditError, match="SKURCZYŁ SIĘ"):
        log.record("api", "auth.login")

    # Dowód pozostaje: kotwica NIE cofnęła się, a świeży odczyt nadal widzi manipulację.
    assert _kotwica(sciezka)["wpisow"] == 5
    swiezy = AuditLog.load(
        sciezka, hmac_key=KLUCZ, anchor_path=sciezka.with_name("audit.log.kotwica")
    )
    assert swiezy.verify() is False


def test_kotwica_ma_ZAPADKE_i_nie_idzie_w_dol(tmp_path: Path) -> None:
    """Druga, NIEZALEŻNA obrona: kotwica nie maleje, choćby stan w pamięci był mniejszy.

    Wołamy `_zapisz_kotwice` wprost, a nie przez `record()`, i to jest tu istotne. Pierwsza
    wersja tego testu szła przez `record()` i **nie sprawdzała zapadki w ogóle**: odświeżenie
    z pliku dociągało brakujące wpisy, zanim kotwica została zapisana, więc mutacja
    usuwająca zapadkę nie czerwieniła testu. Wykryła to dopiero kontrola nośności.

    Mówimy wprost, czego ta asercja dowodzi: w OBECNYM kodzie znaną ścieżkę cofnięcia
    zamyka już kontrola skurczu, więc zapadka jest obroną w głąb. Powód jej istnienia jest
    jednak konkretny — dopóki `_zapisz_kotwice` zapisywało `len(self._entries)`
    bezwarunkowo, DOWOLNA przyszła ścieżka kończąca się mniejszym stanem w pamięci cicho
    cofałaby jedyny dowód odcięcia ogona. Skurcz pliku był pierwszą taką ścieżką, jaką
    znaleziono, a nie jedyną możliwą.
    """
    sciezka = tmp_path / "audit.log"
    _dziennik(sciezka)
    assert _kotwica(sciezka)["wpisow"] == 5, "założenie testu"

    ubogi = AuditLog(
        path=sciezka, hmac_key=KLUCZ, anchor_path=sciezka.with_name("audit.log.kotwica")
    )
    ubogi._zapisz_kotwice()

    assert _kotwica(sciezka)["wpisow"] == 5


def test_kotwica_ROSNIE_normalnie(tmp_path: Path) -> None:
    """Bez tej asercji test wyżej przechodziłby też, gdyby zapadka blokowała KAŻDY zapis."""
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka, wpisow=2)
    przed = _kotwica(sciezka)["wpisow"]

    log.record("api", "kolejny")

    assert _kotwica(sciezka)["wpisow"] == 3
    assert przed != _kotwica(sciezka)["wpisow"]


def test_zywy_obiekt_widzi_obciecie_pliku(tmp_path: Path) -> None:
    """`GET /api/audit` pyta ŻYWY obiekt — musi patrzeć na dysk, nie na pamięć.

    Bez tego okno wykrycia było zerowe, a nie „do następnego wpisu": `verify()` sprawdzało
    wyłącznie stan w pamięci, więc API raportowałoby `verified: true` na obciętym pliku.
    """
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka)
    assert log.verify() is True, "założenie testu"

    _obetnij(sciezka, 2)

    assert log.verify() is False


def test_znikniecie_pliku_jest_ODMOWA_a_nie_nowym_dziennikiem(tmp_path: Path) -> None:
    """Rotacja pliku przy działającym procesie: dopisanie założyłoby dziennik bez początku."""
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka, wpisow=3)
    sciezka.rename(tmp_path / "audit.log.1")

    with pytest.raises(AuditError, match="ZNIKNĄŁ"):
        log.record("api", "po-rotacji")


def test_plik_urosl_ale_stracil_nasza_glowe_lancucha(tmp_path: Path) -> None:
    """Większy plik nie znaczy plik uzupełniony — historia mogła zostać przepisana."""
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka, wpisow=2)
    obcy = AuditLog(path=tmp_path / "obcy.log", hmac_key=KLUCZ)
    for i in range(5):
        obcy.record("api", f"cudza-historia-{i}")
    sciezka.write_text((tmp_path / "obcy.log").read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(AuditError, match="NIE zawiera już wpisu"):
        log.record("api", "kolejny")


# --------------------------------------------------------------------------------------
# Nieczytelny plik — `integrity` tego nie dotyczy
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("integrity", [AuditIntegrity.BLOCKING, AuditIntegrity.WARN])
@pytest.mark.parametrize(
    ("tresc", "opis"),
    [
        ('{"timestamp": "2026-08-28T12:00:00+00:00", "actor": "api", "act\n', "urwana linia"),
        ("[1, 2]\n", "poprawny JSON, ale nie wpis (TypeError)"),
        ('{"timestamp": "t", "actor": "a", "action": "x", "nadmiarowe": 1}\n', "nadmiarowe pole"),
    ],
)
def test_nieczytelny_dziennik_to_ODMOWA_niezaleznie_od_integrity(
    tmp_path: Path, integrity: AuditIntegrity, tresc: str, opis: str
) -> None:
    """`integrity` rozstrzyga o łańcuchu, który się NIE ZGADZA — nie o pliku nie do odczytu.

    Rozróżnienie nie jest formalne: przy niezgodnym łańcuchu wiadomo, co w pliku jest, więc
    operator może świadomie zdecydować o starcie. Gdy pliku nie da się odczytać, nie wiadomo,
    gdzie kończy się łańcuch — a dopisanie skleiłoby dwa łańcuchy w jeden dokument
    wyglądający na kompletny.

    Wariant `[1, 2]` pilnuje osobno `TypeError`: `AuditEntry(**json.loads(...))` rzuca właśnie
    jego, a nie `ValueError`, więc przed poprawką omijał całą obsługę i dawał surowy crash.
    """
    sciezka = tmp_path / "audit.log"
    sciezka.write_text(tresc, encoding="utf-8")

    with pytest.raises(AuditError, match="Nie można odczytać dziennika audytu"):
        build_audit_log(SecurityConfig(audit=AuditConfig(path=sciezka, integrity=integrity)))


def test_nieudany_odczyt_nie_zostawia_polowicznego_stanu(tmp_path: Path) -> None:
    """Wyjątek w połowie wczytywania nie może podmienić stanu na prefiks pliku."""
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka, wpisow=3)
    przed = [w.action for w in log.entries]
    with sciezka.open("a", encoding="utf-8") as uchwyt:
        uchwyt.write('{"timestamp": "urwane\n')

    with pytest.raises(AuditError):
        log.record("api", "kolejny")

    assert [w.action for w in log.entries] == przed


# --------------------------------------------------------------------------------------
# Znacznik rotacji dla dziennika PUSTEGO
# --------------------------------------------------------------------------------------


class _Sekrety:
    """Dostawca sekretów na potrzeby testu."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca materiał dla umówionych referencji."""
        return {"env:NOWY": "klucz-nowy", "env:STARY": "klucz-stary"}.get(ref)


def test_znacznik_rotacji_powstaje_takze_dla_PUSTEGO_dziennika(tmp_path: Path) -> None:
    """Właśnie pusty dziennik jest stanem, w którym okno rotacji stoi najszerzej otworem.

    Komunikat odmowy startu sam prowadzi operatora prosto do tego stanu („zarchiwizuj
    dziennik"), więc wykluczenie go było wykluczeniem przypadku najczęstszego.
    """
    sciezka = tmp_path / "audit.log"

    log = build_audit_log(
        SecurityConfig(
            audit=AuditConfig(
                path=sciezka,
                hmac_key_ref="env:NOWY",
                hmac_key_id="2026-08",
                hmac_verify_keys=[{"id": "", "ref": "env:STARY"}],  # type: ignore[list-item]
            )
        ),
        secrets=_Sekrety(),
    )

    assert [w.action for w in log.entries] == ["audit.key_rotated"]
    assert log.entries[0].key_id == "2026-08"


def test_swieza_instalacja_BEZ_rotacji_nie_dostaje_znacznika(tmp_path: Path) -> None:
    """Bez tej asercji test wyżej przechodziłby też, gdyby znacznik powstawał zawsze."""
    log = build_audit_log(
        SecurityConfig(audit=AuditConfig(path=tmp_path / "audit.log", hmac_key_ref="env:NOWY")),
        secrets=_Sekrety(),
    )

    assert log.entries == []


# --------------------------------------------------------------------------------------
# Blokada plikowa
# --------------------------------------------------------------------------------------


def test_blokada_ma_LIMIT_CZASU_zamiast_zawieszac(tmp_path: Path) -> None:
    """Proces, który padł trzymając blokadę, nie może zamrozić całego audytu.

    Blokada bez limitu zamienia awarię JEDNEGO procesu w zawieszenie wszystkich: każde
    żądanie do API czekałoby w nieskończoność. Odmowa z czytelnym błędem jest gorsza od
    sukcesu, ale znacznie lepsza od cichego zawieszenia.
    """
    plik = tmp_path / "dane.json"
    plik.write_text("{}", encoding="utf-8")

    # Ta sama ścieżka, osobny deskryptor — `flock` traktuje je jak różnych chętnych,
    # więc druga blokada nie ma szans jej zająć i musi odmówić po limicie czasu.
    with (
        blokada_pliku(plik),
        pytest.raises(FileLockError, match="w ciągu"),
        blokada_pliku(plik, limit_sekund=0.2),
    ):
        pass  # pragma: no cover - nieosiągalne, blokada jest zajęta


def test_fsync_dotyczy_PLIKU_DZIENNIKA_a_nie_czegokolwiek(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kontrola strukturalna, ale wskazująca właściwy plik.

    Pierwsza wersja sprawdzała tylko, że wołano JAKIŚ `fsync` — przechodziłaby więc również
    wtedy, gdyby wymuszany był wyłącznie zapis kotwicy, a sam wpis został w buforze.
    Czego ta kontrola NADAL nie dowodzi: że dane przetrwają zanik zasilania. Tego nie da
    się sprawdzić testem — luka zapisana w `docs/BEZPIECZENSTWO.md`.
    """
    sciezka = tmp_path / "audit.log"
    # Plik rozpoznajemy po i-węźle, a nie po nazwie: deskryptora nie da się przenośnie
    # zamienić z powrotem na ścieżkę (`/dev/fd` na macOS tego nie daje).
    zsynchronizowane: list[int] = []
    prawdziwy_fsync = os.fsync

    def _sledzacy(fd: int) -> None:
        zsynchronizowane.append(os.fstat(fd).st_ino)
        prawdziwy_fsync(fd)

    monkeypatch.setattr(os, "fsync", _sledzacy)
    AuditLog(path=sciezka).record("api", "wpis")

    assert sciezka.stat().st_ino in zsynchronizowane, zsynchronizowane


@pytest.mark.parametrize(
    ("przygotuj", "oczekiwany"),
    [
        (lambda p: None, "ok"),
        (lambda p: p.with_name(p.name + ".kotwica").unlink(), "brak"),
        (
            lambda p: p.with_name(p.name + ".kotwica").write_text("{nie json", encoding="utf-8"),
            "nieczytelna",
        ),
    ],
)
def test_stan_kotwicy_jest_WIDOCZNY(tmp_path: Path, przygotuj, oczekiwany: str) -> None:
    """Brak kotwicy WYŁĄCZA wykrywanie odcięcia ogona — i musi to być widać.

    Ta sama instalacja, która przy nieczytelnym DZIENNIKU odmawia startu, przy nieczytelnej
    KOTWICY milczała zupełnie: `verify()` zwracało `True`, bo `_kompletny_wobec_kotwicy`
    traktuje brak danych jak zgodność. Decyzja słuszna (uszkodzenie pliku pomocniczego nie
    może unieważniać dziennika), ale niewidoczna — czyli „fałszywe OK".
    """
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka, wpisow=2)
    przygotuj(sciezka)

    assert log.stan_kotwicy() == oczekiwany
    # Weryfikacja NADAL przechodzi — o to chodzi: to nie jest błąd, tylko utrata kontroli.
    assert log.verify() is True


def test_dziennik_bez_kotwicy_melduje_wylaczona(tmp_path: Path) -> None:
    """Czwarty stan: kotwicy nie skonfigurowano wcale (np. dziennik wczytany do wglądu)."""
    assert AuditLog(path=tmp_path / "audit.log").stan_kotwicy() == "wylaczona"
