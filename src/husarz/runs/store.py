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
import os
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from husarz.runs.records import OrchestrationRecord, RunRecord


@runtime_checkable
class RunStore(Protocol):
    """Szew magazynu przebiegów — wstrzykiwalny, żeby testy działały bez dysku."""

    def save(self, record: RunRecord | OrchestrationRecord) -> None:
        """Utrwala przebieg. Implementacja NIE może rzucać — pomiar nie wywraca pracy agenta."""
        ...


class NullRunStore:
    """Domyślny magazyn: pomiar wyłączony. Nie zapisuje niczego i nic nie kosztuje."""

    def save(self, record: RunRecord | OrchestrationRecord) -> None:  # noqa: D102
        return None


class JsonlRunStore:
    """Zapis do pliku JSONL (jeden przebieg na linię), dopisywany atomowo pod blokadą.

    Format jest celowo ten sam co dziennika audytu — linia JSON — bo narzędzia operatora
    (``grep``, ``jq``, wczytanie do pandas) działają wtedy na obu bez osobnej obsługi.

    Zakres blokady: chroni przed przeplotem WEWNĄTRZ jednego procesu. Gdy do tego samego pliku
    pisze wiele instancji albo wiele procesów (uvicorn z workerami), atomowość opiera się na
    ``O_APPEND`` systemu plików — wystarczająco dla rekordów rzędu kilobajta na lokalnym
    systemie plików, zawodnie na NFS/SMB albo przy mocno podniesionym ``max_iterations``.
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

    def save(self, record: RunRecord | OrchestrationRecord) -> None:
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
                # Prawa zawężone: dane o pracy operatora nie muszą być czytelne dla innych
                # kont na maszynie. `mode` obowiązuje tylko przy TWORZENIU (i jest ignorowane
                # na Windows), więc nie naprawia to katalogów założonych wcześniej.
                self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                fd = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
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


def build_run_store_from_config(config: Any, *, data_dir: str | Path | None = None) -> RunStore:
    """Buduje magazyn przebiegów z pełnej konfiguracji Husarza.

    Jedno miejsce rozwiązywania ścieżki dla WSZYSTKICH konsumentów (pętla narzędziowa,
    orkiestrator, API). Bez tego każdy liczyłby domyślną lokalizację po swojemu i pomiar
    agenta trafiałby gdzie indziej niż pomiar orkiestracji — czyli nie dałoby się ich
    połączyć po ``run_id``, a to jest cały sens wspólnego identyfikatora.

    Args:
        config: wczytana konfiguracja (``HusarzConfig``; typowane luźno, by uniknąć cyklu
            importów z warstwą konfiguracji).
        data_dir: nadpisanie katalogu danych (ścieżka albo napis); ``None`` →
            ``platform.data_dir``.

    Returns:
        Magazyn zgodny z :class:`RunStore`.
    """
    runs_cfg = config.platform.runs
    path = runs_cfg.path
    if runs_cfg.enabled and path is None:
        base = data_dir if data_dir is not None else config.platform.data_dir
        path = Path(base) / "runs" / "runs.jsonl"
    return build_run_store(enabled=runs_cfg.enabled, path=path)
