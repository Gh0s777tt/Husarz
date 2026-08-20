"""Magazyn przebiegów agenta — protokół i implementacje (Etap 16).

Domyślną implementacją jest :class:`NullRunStore`, który NIC nie zapisuje. Zbieranie pomiarów
jest funkcją opt-in, tak jak pętla narzędziowa (ADR-0016) i pamięć trwała (ADR-0018): nowa
instalacja nie zaczyna po cichu produkować plików z danymi o pracy operatora.

Zapisywany rekord niesie wyłącznie metryki (patrz :mod:`husarz.runs.records`), więc plik nie
zawiera promptów ani wyników narzędzi. Mimo to katalog przebiegów traktujemy jak dane
prywatne: `data_dir` jest w `.gitignore`, a plików NIE dołączamy do dokumentacji ani zrzutów.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, runtime_checkable

from husarz.runs.records import RunRecord


@runtime_checkable
class RunStore(Protocol):
    """Szew magazynu przebiegów — wstrzykiwalny, żeby testy działały bez dysku."""

    def save(self, record: RunRecord) -> None:
        """Utrwala przebieg. Implementacja NIE może rzucać — pomiar nie wywraca pracy agenta."""
        ...


class NullRunStore:
    """Domyślny magazyn: pomiar wyłączony. Nie zapisuje niczego i nic nie kosztuje."""

    def save(self, record: RunRecord) -> None:  # noqa: D102 - kontrakt w protokole
        return None


class JsonlRunStore:
    """Zapis do pliku JSONL (jeden przebieg na linię), dopisywany atomowo pod blokadą.

    Format jest celowo ten sam co dziennika audytu — linia JSON — bo narzędzia operatora
    (``grep``, ``jq``, wczytanie do pandas) działają wtedy na obu bez osobnej obsługi.
    Świadomie NIE budujemy łańcucha skrótów: to dane pomiarowe, nie ślad rozliczalności.
    Rozliczalnością zajmuje się audyt, który ma tamper-evidence.

    Attributes:
        path: plik docelowy; katalogi nadrzędne tworzone przy pierwszym zapisie.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        """Ścieżka pliku z przebiegami."""
        return self._path

    def save(self, record: RunRecord) -> None:
        """Dopisuje przebieg jako jedną linię JSON.

        Błąd zapisu (brak uprawnień, pełny dysk, wyścig o katalog) jest POŁYKANY: pomiar
        jakości nie może wywrócić pracy agenta ani zamienić się w awarię produkcyjną.
        Utrata pomiaru jest kosztem akceptowalnym — utrata odpowiedzi agenta nie jest.

        Args:
            record: przebieg do utrwalenia.
        """
        line = json.dumps(asdict(record), ensure_ascii=False, sort_keys=True)
        try:
            with self._lock:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            return None


def build_run_store(*, enabled: bool, path: Path | None) -> RunStore:
    """Buduje magazyn przebiegów z konfiguracji.

    Args:
        enabled: czy zbieranie pomiarów jest włączone (``runs.enabled``).
        path: plik docelowy; ``None`` przy wyłączonym pomiarze.

    Returns:
        :class:`JsonlRunStore` gdy włączone i ścieżka podana, w przeciwnym razie
        :class:`NullRunStore`. Brak ścieżki przy włączonym pomiarze NIE jest błędem
        krytycznym — degradujemy do braku zapisu, bo pomiar nie jest funkcją krytyczną.
    """
    if enabled and path is not None:
        return JsonlRunStore(path)
    return NullRunStore()
