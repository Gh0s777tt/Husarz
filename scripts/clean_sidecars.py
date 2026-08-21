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

Użycie::

    python scripts/clean_sidecars.py          # usuń
    python scripts/clean_sidecars.py --dry-run  # tylko pokaż

BEZPIECZNIK: kasujemy wyłącznie pliki o sygnaturze AppleDouble (``0x00051607``). Plik
o zbieżnej nazwie, ale innej treści, zostaje nietknięty i jest raportowany.
"""

from __future__ import annotations

import argparse
from pathlib import Path

# Magiczne bajty nagłówka AppleDouble — jedyny bezpieczny wyróżnik.
APPLEDOUBLE_MAGIC = b"\x00\x05\x16\x07"

# Katalogi pomijane: wnętrze gita ma własne sidecary, których kasowanie potrafi uszkodzić
# indeks paczek (obserwowane: „error: non-monotonic index" po skasowaniu ._pack-*.idx).
POMIJANE = {".git"}


def znajdz_sidecary(root: Path) -> tuple[list[Path], list[Path]]:
    """Rozdziela pliki ``._*`` na prawdziwe sidecary i pozostałe.

    Args:
        root: katalog, od którego zaczynamy przeszukiwanie.

    Returns:
        Para (sidecary AppleDouble, pliki o zbieżnej nazwie ale innej treści).
    """
    sidecary: list[Path] = []
    obce: list[Path] = []
    for sciezka in root.rglob("._*"):
        if POMIJANE & set(sciezka.parts):
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
    args = parser.parse_args(argv)

    sidecary, obce = znajdz_sidecary(args.root)
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
