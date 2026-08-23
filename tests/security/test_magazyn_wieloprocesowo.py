"""Magazyn sekretów przy DWÓCH procesach na tym samym pliku.

**Skąd ten plik.** Ostatnia pozycja z serii przeglądów Etapu 17: magazyn zakładał wyłączność
jednego procesu, ale nic jej nie egzekwował. Dwa procesy Husarza wskazujące ten sam plik
gubiły sobie zapisy — każdy startował od SWOJEJ kopii wpisów, więc zapis drugiego nadpisywał
plik wersją bez sekretu zapisanego przez pierwszy. Objawiało się to jako „token przestał
działać", bez żadnego błędu.

Testy używają PRAWDZIWYCH procesów potomnych, nie wątków. Blokada jest międzyprocesowa
(``flock``), a wątki w jednym procesie nie odtworzyłyby jej działania: `flock` jest zakładany
per deskryptor, więc test wątkowy mógłby przejść z zupełnie innego powodu.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from husarz.security.secret_store import SCHEME, build_secret_store

pytestmark = pytest.mark.security

_KLUCZ = "klucz-glowny-testowy"


class _DictSecrets:
    """Dostawca klucza głównego."""

    def resolve(self, ref: str) -> str | None:
        """Zwraca klucz dla umówionej referencji."""
        return _KLUCZ if ref == "env:KLUCZ" else None


def _magazyn(sciezka: Path):  # noqa: ANN202 - typ zwracany oczywisty z build_secret_store
    return build_secret_store(path=sciezka, key_ref="env:KLUCZ", secrets=_DictSecrets())


_SKRYPT_POTOMNY = textwrap.dedent("""
    import sys
    sys.path.insert(0, {repo!r})
    from pathlib import Path
    from husarz.security.secret_store import build_secret_store

    class S:
        def resolve(self, ref):
            return {klucz!r} if ref == "env:KLUCZ" else None

    magazyn = build_secret_store(path=Path({sciezka!r}), key_ref="env:KLUCZ", secrets=S())
    magazyn.put({nazwa!r}, {wartosc!r})
    """)


def _zapisz_w_osobnym_procesie(sciezka: Path, nazwa: str, wartosc: str) -> None:
    """Uruchamia PRAWDZIWY drugi proces, który dopisuje sekret do tego samego pliku."""
    kod = _SKRYPT_POTOMNY.format(
        repo=str(Path("src").resolve()),
        klucz=_KLUCZ,
        sciezka=str(sciezka),
        nazwa=nazwa,
        wartosc=wartosc,
    )
    wynik = subprocess.run(  # noqa: S603 - interpreter bieżącego środowiska, kod generowany tu
        [sys.executable, "-c", kod], capture_output=True, text=True, timeout=120, check=False
    )
    assert wynik.returncode == 0, f"proces potomny padł: {wynik.stderr[-500:]}"


def test_zapis_drugiego_procesu_nie_kasuje_sekretu_pierwszego(tmp_path: Path) -> None:
    """Regresja: to jest dokładnie ta cicha utrata, dla której powstała blokada.

    Bez odczytu-modyfikacji-zapisu pod blokadą drugi proces zapisywał plik na podstawie
    SWOJEJ (pustej) kopii wpisów, kasując sekret pierwszego.
    """
    plik = tmp_path / "store.json"
    pierwszy = _magazyn(plik)
    pierwszy.put("a", "wartosc-A")

    _zapisz_w_osobnym_procesie(plik, "b", "wartosc-B")

    na_dysku = json.loads(plik.read_text(encoding="utf-8"))["entries"]
    assert sorted(na_dysku) == ["a", "b"], "zapis drugiego procesu skasował sekret pierwszego"


def test_pierwszy_proces_widzi_sekret_dopisany_przez_drugi(tmp_path: Path) -> None:
    """Kopia w pamięci nie może starzeć się w milczeniu.

    Bez przeładowania po zmianie pliku sekret dopisany przez drugi proces byłby dla pierwszego
    nieistniejący — a połączenie Git utworzone tam nie działałoby tutaj.
    """
    plik = tmp_path / "store.json"
    pierwszy = _magazyn(plik)
    pierwszy.put("a", "wartosc-A")

    _zapisz_w_osobnym_procesie(plik, "b", "wartosc-B")

    assert pierwszy.resolve(f"{SCHEME}b") == "wartosc-B"
    assert sorted(pierwszy.names()) == ["a", "b"]


def test_wlasny_zapis_po_zapisie_obcym_nie_gubi_zadnego(tmp_path: Path) -> None:
    """Najostrzejszy przypadek: piszemy PO tym, jak ktoś inny dopisał coś do pliku.

    Bez ponownego odczytu pod blokadą nasz zapis oparłby się na nieaktualnej kopii i skasował
    wpis obcego procesu — czyli utrata zachodziłaby w drugą stronę.
    """
    plik = tmp_path / "store.json"
    pierwszy = _magazyn(plik)
    pierwszy.put("a", "wartosc-A")
    _zapisz_w_osobnym_procesie(plik, "b", "wartosc-B")

    pierwszy.put("c", "wartosc-C")

    na_dysku = json.loads(plik.read_text(encoding="utf-8"))["entries"]
    assert sorted(na_dysku) == ["a", "b", "c"], na_dysku


def test_usuwanie_tez_startuje_od_stanu_z_dysku(tmp_path: Path) -> None:
    """`delete` również jest odczytem-modyfikacją-zapisem, nie zapisem z pamięci."""
    plik = tmp_path / "store.json"
    pierwszy = _magazyn(plik)
    pierwszy.put("a", "wartosc-A")
    _zapisz_w_osobnym_procesie(plik, "b", "wartosc-B")

    assert pierwszy.delete("a") is True

    na_dysku = json.loads(plik.read_text(encoding="utf-8"))["entries"]
    assert sorted(na_dysku) == ["b"], "usunięcie skasowało też wpis obcego procesu"


def test_plik_blokady_powstaje_obok_i_jest_pusty(tmp_path: Path) -> None:
    """Blokada trzyma OSOBNY plik — magazyn jest podmieniany przez `os.replace`.

    Blokada założona na samym magazynie dotyczyłaby po podmianie i-węzła, którego już nikt
    nie widzi, więc nie chroniłaby niczego.
    """
    plik = tmp_path / "store.json"
    _magazyn(plik).put("a", "wartosc-A")

    blokada = tmp_path / "store.json.lock"
    assert blokada.is_file()
    assert blokada.stat().st_size == 0, "plik blokady nie może niczego przechowywać"


_SKRYPT_TRZYMAJACY_BLOKADE = textwrap.dedent("""
    import fcntl, os, sys, time
    from pathlib import Path

    blokada = Path({blokada!r})
    blokada.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(blokada, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    Path({gotowy!r}).write_text("trzymam", encoding="utf-8")
    time.sleep({sekundy!r})
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)
    """)


@pytest.mark.skipif(sys.platform == "win32", reason="test używa fcntl (POSIX)")
def test_zapis_CZEKA_na_zwolnienie_blokady_przez_inny_proces(tmp_path: Path) -> None:
    """Dowód, że blokada faktycznie WYKLUCZA, a nie tylko istnieje jako plik.

    Poprzednie testy są sekwencyjne — proces potomny kończy się, zanim rodzic zacznie pisać —
    więc blokada nigdy nie jest w sporze i mogłyby przejść nawet po jej usunięciu. Wykryte
    kontrolą nośności: mutacja zdejmująca `flock` nie czerwieniła żadnego z nich.

    Tutaj proces potomny TRZYMA blokadę przez ustalony czas, a my mierzymy, czy zapis rodzica
    faktycznie na nią zaczekał.
    """
    plik = tmp_path / "store.json"
    magazyn = _magazyn(plik)
    gotowy = tmp_path / "gotowy"
    trzymanie = 1.0

    kod = _SKRYPT_TRZYMAJACY_BLOKADE.format(
        blokada=str(plik) + ".lock", gotowy=str(gotowy), sekundy=trzymanie
    )
    potomny = subprocess.Popen(  # noqa: S603 - interpreter bieżącego środowiska
        [sys.executable, "-c", kod], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    try:
        # Czekamy, aż potomny NA PEWNO trzyma blokadę — inaczej mierzylibyśmy wyścig startu.
        start_oczekiwania = time.monotonic()
        while not gotowy.exists():
            if time.monotonic() - start_oczekiwania > 10:
                raise AssertionError("proces potomny nie zdążył zająć blokady")
            time.sleep(0.01)

        start = time.monotonic()
        magazyn.put("a", "wartosc-A")
        czekano = time.monotonic() - start
    finally:
        potomny.wait(timeout=30)

    assert czekano >= trzymanie * 0.4, (
        f"zapis nie czekał na blokadę (zajął {czekano:.3f} s, "
        f"a blokada była trzymana {trzymanie} s) — wzajemne wykluczanie NIE działa"
    )
    assert magazyn.resolve(f"{SCHEME}a") == "wartosc-A"
