"""Dziennik audytu przy DWÓCH procesach na tym samym pliku.

**Skąd ten plik — i to jest ważne, bo nie z przeglądu, tylko z awarii.** Przy wprowadzaniu
blokującej kontroli integralności (Etap 18) trzynaście testów zaczęło padać na tym samym
komunikacie: realny dziennik projektu, `audit/audit.log`, nie przechodził weryfikacji.
Diagnoza wykazała, że nikt niczego nie fałszował — wpis nr 261 wskazywał na skrót wpisu
nr 256, POMIJAJĄC cztery wpisy zapisane w międzyczasie:

    256  11:20:03  doctor                    prev=e69c2c4a  hash=7f3ccf81
    257  11:25:03  config.runtime_override   prev=7f3ccf81   <- proces A
    ...
    260  11:25:47  config.runtime_override   prev=821b8e3b   <- proces A
    261  11:28:21  orchestrate               prev=7f3ccf81   <- proces B, głowa sprzed A

`AuditLog` miał blokadę WĄTKOWĄ (pula wątków FastAPI), lecz trzymał `_last_hash` w pamięci
i nie sprawdzał, czy plik urósł. Dwa procesy Husarza wskazujące tę samą ścieżkę rozgałęziały
więc łańcuch — cicho, bo dopiero weryfikacja to pokazywała, i myląco, bo wyglądało to
dokładnie jak manipulacja. Dla dziennika audytu fałszywy alarm jest kosztowny osobno: uczy
operatora, że alarmom nie warto wierzyć.

Naprawa ma DWIE części i obie są konieczne. Blokada plikowa szereguje dopisywanie, ale sama
nie wystarcza: proces pod blokadą musi jeszcze PONOWNIE odczytać plik, bo jego głowa łańcucha
pochodzi sprzed jej zajęcia. Test `test_stara_glowa_lancucha_jest_ODSWIEZANA` pilnuje właśnie
tej drugiej części — bez niej sama blokada dawałaby fałszywe poczucie naprawy.

Testy używają PRAWDZIWYCH procesów potomnych. `flock` zakłada się per deskryptor, więc test
wątkowy mógłby przejść z zupełnie innego powodu.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from husarz.security.audit import AuditLog

pytestmark = pytest.mark.security

_REPO = str(Path(__file__).resolve().parents[2] / "src")

_SKRYPT_POTOMNY = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from pathlib import Path
    from husarz.security.audit import AuditLog

    sciezka = Path({sciezka!r})
    log = AuditLog(path=sciezka, anchor_path=sciezka.with_name(sciezka.name + ".kotwica"))
    log._wczytaj(sciezka)
    for i in range({ile}):
        log.record("api", "{prefiks}-%d" % i, {{"i": i}})
    """)


def _dopisz_w_osobnym_procesie(sciezka: Path, prefiks: str, ile: int = 3) -> None:
    """Uruchamia PRAWDZIWY drugi proces dopisujący do tego samego dziennika."""
    skrypt = _SKRYPT_POTOMNY.format(repo=_REPO, sciezka=str(sciezka), prefiks=prefiks, ile=ile)
    wynik = subprocess.run(  # noqa: S603
        [sys.executable, "-c", skrypt], capture_output=True, text=True, timeout=60, check=False
    )
    assert wynik.returncode == 0, wynik.stderr


def _dziennik(sciezka: Path) -> AuditLog:
    """Dziennik z kotwicą pod wskazaną ścieżką."""
    return AuditLog(path=sciezka, anchor_path=sciezka.with_name(sciezka.name + ".kotwica"))


def test_stara_glowa_lancucha_jest_ODSWIEZANA(tmp_path: Path) -> None:
    """Sedno wady: instancja z NIEAKTUALNĄ głową nie może dopisać obok łańcucha.

    Odtwarza dokładnie układ z realnego dziennika: dwie instancje wczytują ten sam plik,
    pierwsza dopisuje, a druga — nieświadoma tego — dopisuje po niej.
    """
    sciezka = tmp_path / "audit.log"
    pierwszy = _dziennik(sciezka)
    pierwszy.record("api", "wspolny")

    drugi = _dziennik(sciezka)  # wczytuje stan, w którym jest jeden wpis…
    drugi._wczytaj(sciezka)
    pierwszy.record("api", "dopisek-pierwszego")  # …po czym pierwszy dopisuje

    drugi.record("api", "dopisek-drugiego")

    wpisy = AuditLog.load(sciezka).entries
    assert [w.action for w in wpisy] == ["wspolny", "dopisek-pierwszego", "dopisek-drugiego"]
    # Właściwa asercja: łańcuch jest CIĄGŁY, a nie rozgałęziony.
    assert wpisy[2].prev_hash == wpisy[1].entry_hash
    assert AuditLog.load(sciezka, anchor_path=sciezka.with_name("audit.log.kotwica")).verify()


def test_dwa_PROCESY_nie_rozgalezaja_lancucha(tmp_path: Path) -> None:
    """To samo, ale na prawdziwych procesach — bo blokada jest międzyprocesowa."""
    sciezka = tmp_path / "audit.log"
    rodzic = _dziennik(sciezka)
    rodzic.record("api", "rodzic-0")

    _dopisz_w_osobnym_procesie(sciezka, "potomek", ile=3)
    rodzic.record("api", "rodzic-1")

    log = AuditLog.load(sciezka, anchor_path=sciezka.with_name("audit.log.kotwica"))
    assert [w.action for w in log.entries] == [
        "rodzic-0",
        "potomek-0",
        "potomek-1",
        "potomek-2",
        "rodzic-1",
    ]
    assert log.verify() is True


def test_kotwica_zlicza_wpisy_obu_procesow(tmp_path: Path) -> None:
    """Kotwica po odświeżeniu liczy CAŁY plik, a nie wpisy własnego procesu.

    Bez tej asercji naprawa mogłaby zszyć łańcuch, zostawiając kotwicę na stanie sprzed
    dopisków obcego procesu — czyli trwały fałszywy alarm odcięcia ogona.
    """
    sciezka = tmp_path / "audit.log"
    kotwica = sciezka.with_name("audit.log.kotwica")
    rodzic = _dziennik(sciezka)
    rodzic.record("api", "rodzic-0")
    _dopisz_w_osobnym_procesie(sciezka, "potomek", ile=2)

    rodzic.record("api", "rodzic-1")

    dane = json.loads(kotwica.read_text(encoding="utf-8"))
    linie = [w for w in sciezka.read_text(encoding="utf-8").splitlines() if w.strip()]
    assert dane["wpisow"] == len(linie) == 4
    assert dane["skrot"] == json.loads(linie[-1])["entry_hash"]


def test_wpis_jest_czytelny_z_pliku_po_powrocie_z_record(tmp_path: Path) -> None:
    """`record` obiecuje persist-first: stan w pamięci zmienia się PO zapisie."""
    sciezka = tmp_path / "audit.log"
    log = _dziennik(sciezka)

    wpis = log.record("api", "utrwalony")

    z_dysku = json.loads(sciezka.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert z_dysku["entry_hash"] == wpis.entry_hash


def test_dopisanie_wymusza_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Kontrola STRUKTURALNA — i mówimy wprost, czego NIE dowodzi.

    Trwałości zapisu nie da się sprawdzić testem: wymagałaby odcięcia zasilania w środku
    operacji. Test wyżej (`..._czytelny_z_pliku_...`) przechodzi także BEZ `fsync`, bo po
    zamknięciu pliku dane są już w buforze systemu i odczyt je widzi — sprawdzone kontrolą
    nośności, mutacja usuwająca `fsync` nie zaczerwieniła go.

    Zostaje więc kontrola słabsza: czy `fsync` jest w ogóle wołany dla pliku dziennika.
    NIE dowodzi ona, że dane przetrwają zanik zasilania (to zależy od systemu plików,
    kontrolera dysku i jego pamięci podręcznej) — dowodzi tylko, że kod o to prosi.
    Luka jest zapisana w `docs/BEZPIECZENSTWO.md`.
    """
    sciezka = tmp_path / "audit.log"
    wolane: list[int] = []
    prawdziwy = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (wolane.append(fd), prawdziwy(fd))[1])

    _dziennik(sciezka).record("api", "utrwalony")

    assert wolane, "dopisanie wpisu nie wywołało fsync"
