"""Sprzątanie sidecarów AppleDouble — w tym wewnątrz `.git`.

**Skąd ten plik.** Dysk z repozytorium odłączył się w trakcie pracy. Pierwszą rzeczą, po którą
się wtedy sięga, jest `git fsck` — a ten zwrócił 539 linii `badRefName` i „zły plik SHA-1",
wszystkie o plikach `._*`. Narzędzie do sprawdzania spójności przestawało być czytelne
dokładnie w sytuacji, dla której istnieje. Skrypt pomijał wtedy `.git` z uzasadnienia, którego
nie dało się odtworzyć (patrz sprostowanie w docstringu skryptu).

Testujemy czystą logikę wyboru plików — bez dotykania prawdziwego repozytorium.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MAGIA = b"\x00\x05\x16\x07"


def _modul():  # noqa: ANN202 - typ modułu nieistotny dla testu
    """Ładuje skrypt operatora z `scripts/` (nie jest pakietem, więc po ścieżce)."""
    sciezka = Path("scripts/clean_sidecars.py").resolve()
    spec = importlib.util.spec_from_file_location("clean_sidecars", sciezka)
    assert spec is not None and spec.loader is not None
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _drzewo(tmp_path: Path) -> Path:
    """Buduje układ plików odwzorowujący realny przypadek z exFAT."""
    (tmp_path / ".git" / "refs" / "heads").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "ab").mkdir(parents=True)
    (tmp_path / ".git" / "objects" / "pack").mkdir(parents=True)
    (tmp_path / "src").mkdir()
    # Prawdziwe sidecary AppleDouble.
    for wzgledna in [
        "src/._main.py",
        ".git/refs/heads/._main",
        ".git/objects/ab/._cdef",
        ".git/objects/pack/._pack-abc.idx",
    ]:
        (tmp_path / wzgledna).write_bytes(_MAGIA + b"metadane")
    # Plik o zbieżnej nazwie, ale BEZ sygnatury — nie wolno go ruszać.
    (tmp_path / "src" / "._nie_sidecar.txt").write_bytes(b"zwykla tresc")
    return tmp_path


def test_domyslnie_wnetrze_gita_zostaje_nietkniete(tmp_path: Path) -> None:
    """Zachowanie sprzed zmiany musi zostać domyślne: sprzątanie przed `docker build`
    nie ma powodu wchodzić do `.git`."""
    root = _drzewo(tmp_path)

    sidecary, _ = _modul().znajdz_sidecary(root)

    nazwy = {str(p.relative_to(root)) for p in sidecary}
    assert nazwy == {"src/._main.py"}


def test_include_git_obejmuje_refy_i_obiekty(tmp_path: Path) -> None:
    """To one zasypują `git fsck` — bez nich flaga nie rozwiązywałaby problemu, dla
    którego powstała."""
    root = _drzewo(tmp_path)

    sidecary, _ = _modul().znajdz_sidecary(root, include_git=True)

    nazwy = {str(p.relative_to(root)) for p in sidecary}
    assert ".git/refs/heads/._main" in nazwy
    assert ".git/objects/ab/._cdef" in nazwy
    assert "src/._main.py" in nazwy


def test_katalog_paczek_zostaje_pomijany_ZAWSZE(tmp_path: Path) -> None:
    """Nawet z `--include-git`: git zarządza `objects/pack` sam.

    Zmierzone: `git count-objects -v` z podłożonym `._pack-<hash>.idx` zgłasza
    „warning: no corresponding .pack" i zalicza plik do `garbage`, a `git gc` usuwa go
    przy najbliższym przebiegu. Dublowanie tego mechanizmu skryptem operatora nie ma sensu.
    """
    root = _drzewo(tmp_path)

    sidecary, _ = _modul().znajdz_sidecary(root, include_git=True)

    nazwy = {str(p.relative_to(root)) for p in sidecary}
    assert ".git/objects/pack/._pack-abc.idx" not in nazwy


def test_wylaczenie_paczek_nie_lapie_zwyklych_katalogow_projektu(tmp_path: Path) -> None:
    """`objects/` i `pack/` mogą istnieć w drzewie projektu i nie mają związku z gitem.

    Dlatego wyłączenie sprawdza PARĘ sąsiadujących segmentów, a nie same nazwy — inaczej
    `assets/pack/._logo.png` przetrwałoby sprzątanie bez powodu.
    """
    root = _drzewo(tmp_path)
    (root / "assets" / "pack").mkdir(parents=True)
    (root / "assets" / "pack" / "._logo.png").write_bytes(_MAGIA + b"x")
    (root / "objects").mkdir()
    (root / "objects" / "._dane.bin").write_bytes(_MAGIA + b"x")

    sidecary, _ = _modul().znajdz_sidecary(root, include_git=True)

    nazwy = {str(p.relative_to(root)) for p in sidecary}
    assert "assets/pack/._logo.png" in nazwy
    assert "objects/._dane.bin" in nazwy


def test_plik_bez_sygnatury_NIE_jest_usuwany(tmp_path: Path) -> None:
    """Bezpiecznik: kasujemy wyłącznie po sygnaturze AppleDouble, nie po nazwie."""
    root = _drzewo(tmp_path)

    sidecary, obce = _modul().znajdz_sidecary(root, include_git=True)

    assert not [p for p in sidecary if p.name == "._nie_sidecar.txt"]
    assert [p for p in obce if p.name == "._nie_sidecar.txt"]
