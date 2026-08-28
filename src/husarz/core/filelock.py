"""Blokada plikowa MIĘDZYPROCESOWA — wspólna dla magazynów i dziennika audytu.

Dlaczego w warstwie 0. Blokada nie ma nic wspólnego z bezpieczeństwem ani konfiguracją:
to prymityw systemu plików. Trzymanie jej w ``husarz.security.secret_store`` (gdzie
powstała) znaczyło, że dziennik audytu musiałby importować magazyn sekretów, żeby
skorzystać z pięciu linii kodu — czyli albo cykl importów, albo druga kopia tego samego.

**Czego ta blokada NIE robi.** Nie zapewnia spójności sama z siebie. Proces, który
trzyma stan w pamięci (jak łańcuch skrótów audytu albo zawartość magazynu), musi pod
blokadą PONOWNIE odczytać plik — inaczej zapisze na podstawie danych sprzed blokady.
Ta wada wystąpiła w tym projekcie naprawdę: dziennik audytu miał blokadę wątkową, więc
dwa procesy Husarza dopisywały równolegle, każdy ze swoim ``_last_hash``, i łańcuch
rozgałęział się bez śladu. Sama blokada by tego nie naprawiła.

Blokada jest doradcza (``flock``/``msvcrt``): działa wobec procesów, które też jej
używają. Nie chroni przed edycją pliku edytorem tekstu — od tego jest łańcuch skrótów.

**NIE jest reentrantna.** Każde wejście otwiera własny deskryptor, a ``flock`` wiąże blokadę
z deskryptorem, nie z procesem — zagnieżdżenie dwóch bloków ``with`` na tej samej ścieżce
w JEDNYM procesie kończy się więc czekaniem na samego siebie aż do limitu czasu. Wołający ma
pilnować, żeby wejścia się nie zagnieżdżały; w dzienniku audytu robi to podział ról:
``record`` zajmuje blokadę, a ``_odswiez_z_pliku`` zakłada, że już ją ma.
"""

from __future__ import annotations

import contextlib
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class FileLockError(Exception):
    """Nie udało się utworzyć albo zająć blokady pliku.

    Warstwy wyższe tłumaczą go na własny typ błędu (``SecretStoreError``, ``AuditError``),
    tak jak :class:`~husarz.core.errors.CryptoError` — kontrakt modułu wołającego nie może
    zależeć od tego, skąd wzięto prymityw.
    """


#: Ile najdłużej czekamy na zwolnienie blokady, zanim uznamy ją za zakleszczoną.
#: Blokada bez limitu czasu zamienia awarię JEDNEGO procesu w zawieszenie wszystkich:
#: proces, który padł trzymając `flock`, wstrzymywałby każdy zapis audytu w nieskończoność,
#: a każde żądanie do API czekałoby w nieskończoność razem z nim. Odmowa z czytelnym błędem
#: jest gorsza od sukcesu, ale znacznie lepsza od cichego zawieszenia.
DOMYSLNY_LIMIT_SEKUND = 10.0

#: Odstęp między próbami zajęcia blokady.
_ODSTEP_SEKUND = 0.05


@contextmanager
def blokada_pliku(sciezka: Path, *, limit_sekund: float = DOMYSLNY_LIMIT_SEKUND) -> Iterator[None]:
    """Blokada wyłączna na czas operacji na pliku ``sciezka``.

    Blokujemy PLIK OBOK (``<nazwa>.lock``), nie sam plik danych. Powód jest praktyczny:
    plik danych bywa podmieniany atomowo przez ``replace``, co unieważniłoby deskryptor
    trzymający blokadę — a plik ``.lock`` trwa niezależnie od tych podmian.

    Args:
        sciezka: Ścieżka pliku danych, którego dotyczy blokada.
        limit_sekund: Ile najdłużej czekać na zwolnienie blokady.

    Yields:
        Nic — blokada obowiązuje w obrębie bloku ``with``.

    Raises:
        FileLockError: Gdy pliku blokady nie da się utworzyć, zablokować albo gdy upłynął
            limit czasu.
    """
    plik_blokady = sciezka.with_name(sciezka.name + ".lock")
    plik_blokady.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(plik_blokady, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise FileLockError(f"Nie można utworzyć blokady {plik_blokady}: {exc}") from exc
    try:
        _zajmij(fd, plik_blokady, limit_sekund)
        yield
    finally:
        _zwolnij(fd)
        os.close(fd)


def _zajmij(fd: int, plik_blokady: Path, limit_sekund: float) -> None:
    """Zakłada blokadę wyłączną, czekając NAJWYŻEJ ``limit_sekund``.

    Czekamy w pętli prób nieblokujących zamiast jednym wywołaniem blokującym, bo to
    jedyny sposób, żeby dotrzymać limitu przenośnie — ani ``flock``, ani
    ``msvcrt.locking`` nie przyjmują limitu czasu.

    Args:
        fd: Deskryptor pliku blokady.
        plik_blokady: Ścieżka — wyłącznie do komunikatu błędu.
        limit_sekund: Górna granica oczekiwania.

    Raises:
        FileLockError: Gdy blokady nie da się zająć w wyznaczonym czasie.
    """
    koniec = time.monotonic() + max(0.0, limit_sekund)
    while True:
        blad = _sprobuj_zajac(fd)
        if blad is None:
            return
        if time.monotonic() >= koniec:
            raise FileLockError(
                f"Nie udało się zająć blokady {plik_blokady} w ciągu {limit_sekund:g} s "
                f"({blad}). Najczęstsza przyczyna: inny proces Husarza trzyma ją, bo zawisł "
                f"albo padł, nie zwalniając jej. Sprawdź, czy nie działa druga instancja."
            )
        time.sleep(_ODSTEP_SEKUND)


def _sprobuj_zajac(fd: int) -> OSError | None:
    """Jedna próba NIEBLOKUJĄCA. Zwraca błąd, gdy blokada jest zajęta, albo ``None``."""
    if sys.platform == "win32":  # pragma: no cover - nieweryfikowane na macOS
        import msvcrt  # noqa: PLC0415

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            return exc
        return None
    import fcntl  # noqa: PLC0415

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return exc
    return None


def _zwolnij(fd: int) -> None:
    """Zwalnia blokadę. Awaria zwolnienia nie może przesłonić właściwego błędu operacji.

    Args:
        fd: Deskryptor pliku blokady.
    """
    if sys.platform == "win32":  # pragma: no cover - nieweryfikowane na macOS
        import msvcrt  # noqa: PLC0415

        with contextlib.suppress(OSError):
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl  # noqa: PLC0415

    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
