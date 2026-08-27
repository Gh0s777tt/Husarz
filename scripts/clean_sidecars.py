#!/usr/bin/env python3
"""Usuwanie sidecarów AppleDouble (``._*``) — narzędzie operatora dla macOS.

**Po co to istnieje.** macOS zapisuje rozszerzone atrybuty plików w osobnych plikach ``._*``,
gdy wolumen ich nie obsługuje natywnie (exFAT, NTFS, dyski sieciowe). Na takim wolumenie
``docker build`` **przerywa** już przy wysyłaniu kontekstu::

    failed to xattr ._CHANGELOG.md: operation not permitted

Wpis ``._*`` w ``.dockerignore`` tego NIE naprawia — zweryfikowane empirycznie: z sidecarem
build kończy się kodem 1 mimo wpisu, bez sidecara kodem 0. Błąd powstaje w nadawcy kontekstu,
zanim reguły ignorowania zostaną zastosowane. Wpis w ``.dockerignore`` zostaje, bo jest
poprawny co do zasady (nie wpuszcza śmieci do obrazu), ale sam problem rozwiązuje wyłącznie
usunięcie plików przed budową.

Sidecary odrastają przy każdym zapisie na wolumen, więc to krok do powtarzania — nie
jednorazowe sprzątanie.

**Sidecary wewnątrz ``.git`` (flaga ``--include-git``).** Powstają tam tak samo jak wszędzie
indziej i psują dokładnie jedno, ale ważne narzędzie: ``git fsck``. Po jednym cyklu pracy na
exFAT było ich 552, a ``git fsck`` zwracał 539 linii ``badRefName``/``zły plik SHA-1`` — czyli
narzędzie do sprawdzania spójności przestawało być czytelne akurat wtedy, gdy jest potrzebne
(po nagłym odłączeniu dysku). Same operacje gita działają: ``git for-each-ref`` i
``git show-ref`` ich NIE wyliczają (sprawdzone), więc to szum, nie uszkodzenie.

**SPROSTOWANIE.** Poprzednia wersja tego pliku pomijała ``.git`` z uzasadnieniem, że
kasowanie sidecarów „potrafi uszkodzić indeks paczek (obserwowane: ``error: non-monotonic
index`` po skasowaniu ``._pack-*.idx``)". Nie udało się tego odtworzyć — a pomiar pokazuje
zależność ODWROTNĄ: to OBECNOŚĆ takiego pliku sprawia, że git zgłasza ``warning: no
corresponding .pack`` i zalicza go do ``garbage``, po czym ``git gc`` **sam go usuwa**.
Prawdopodobne wyjaśnienie pierwotnej obserwacji: plik zniknął z ręki gita, a skutek przypisano
skasowaniu. Katalog ``objects/pack`` zostaje mimo to wyłączony na stałe — nie z ostrożności,
lecz dlatego, że git zarządza nim sam i dublowanie tego nie ma sensu.

Użycie::

    python scripts/clean_sidecars.py                 # usuń (bez wnętrza .git)
    python scripts/clean_sidecars.py --dry-run       # tylko pokaż
    python scripts/clean_sidecars.py --include-git   # także w .git (przywraca czytelność fsck)

BEZPIECZNIK: kasujemy wyłącznie pliki o sygnaturze AppleDouble (``0x00051607``). Plik
o zbieżnej nazwie, ale innej treści, zostaje nietknięty i jest raportowany.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# Magiczne bajty nagłówka AppleDouble — jedyny bezpieczny wyróżnik.
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"

# Domyślnie pomijamy wnętrze gita. Sidecary powstają tam tak samo jak wszędzie indziej
# (552 sztuki po jednym cyklu pracy na exFAT), ale ich kasowanie nie jest częścią zwykłego
# sprzątania przed budową obrazu — dlatego jest osobną, JAWNĄ decyzją (`--include-git`).
POMIJANE_DOMYSLNIE = {".git"}

# Katalog paczek zostaje pomijany ZAWSZE. Nie z ostrożności „na wszelki wypadek", tylko
# dlatego, że git zarządza nim sam: zmierzone `git count-objects -v` z podłożonym
# `._pack-<hash>.idx` daje „warning: no corresponding .pack" i zalicza plik do `garbage`,
# a `git gc` usuwa go przy najbliższym przebiegu. Wchodzenie tam skryptem operatora byłoby
# dublowaniem mechanizmu, który działa.
ZAWSZE_POMIJANE = {("objects", "pack")}


def _pomijany(sciezka: Path, *, include_git: bool) -> bool:
    """Czy ścieżka leży w katalogu wyłączonym ze sprzątania.

    Args:
        sciezka: kandydat do usunięcia.
        include_git: czy wolno wchodzić do wnętrza ``.git``.

    Returns:
        ``True``, gdy pliku NIE wolno ruszać.
    """
    czesci = sciezka.parts
    # `objects/pack` jest wyłączony ZAWSZE — patrz komentarz przy `ZAWSZE_POMIJANE`.
    # Sprawdzamy PARĘ sąsiadujących segmentów, a nie osobne nazwy: katalog roboczy
    # projektu też może mieć `objects/` albo `pack/`, a te nie mają z gitem nic wspólnego.
    for a, b in ZAWSZE_POMIJANE:
        for i in range(len(czesci) - 1):
            if czesci[i] == a and czesci[i + 1] == b:
                return True
    return not include_git and bool(POMIJANE_DOMYSLNIE & set(czesci))


def znajdz_sidecary(root: Path, *, include_git: bool = False) -> tuple[list[Path], list[Path]]:
    """Rozdziela pliki ``._*`` na prawdziwe sidecary i pozostałe.

    Args:
        root: katalog, od którego zaczynamy przeszukiwanie.
        include_git: czy wejść także do wnętrza ``.git`` (z wyjątkiem ``objects/pack``).

    Returns:
        Para (sidecary AppleDouble, pliki o zbieżnej nazwie ale innej treści).
    """
    sidecary: list[Path] = []
    obce: list[Path] = []
    for sciezka in root.rglob("._*"):
        if _pomijany(sciezka, include_git=include_git):
            continue
        try:
            if sciezka.read_bytes()[:4] == APPLEDOUBLE_MAGIC:
                sidecary.append(sciezka)
            else:
                obce.append(sciezka)
        except OSError:
            continue
    return sidecary, obce


def main(argv: list[str] | None = None) -> int:
    """Punkt wejścia CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="katalog (domyślnie: .)")
    parser.add_argument("--dry-run", action="store_true", help="tylko pokaż, nie usuwaj")
    parser.add_argument(
        "--include-git",
        action="store_true",
        help=(
            "sprzątaj także wewnątrz .git (bez objects/pack). Przywraca czytelność "
            "`git fsck`, który przy setkach sidecarów tonie w błędach badRefName."
        ),
    )
    args = parser.parse_args(argv)

    sidecary, obce = znajdz_sidecary(args.root, include_git=args.include_git)
    if args.dry_run:
        print(f"Do usunięcia: {len(sidecary)} sidecarów AppleDouble.")
    else:
        usuniete = 0
        for sciezka in sidecary:
            try:
                sciezka.unlink()
                usuniete += 1
            except OSError:
                continue
        print(f"Usunięto {usuniete} sidecarów AppleDouble.")
    if obce:
        print(f"POMINIĘTO {len(obce)} plików '._*' bez sygnatury AppleDouble:")
        for sciezka in obce[:10]:
            print(f"  {sciezka}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
