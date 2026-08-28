"""Wyłącznik bezpiecznikowy routera — model, który właśnie zawiódł, spada na koniec.

**Problem, który to rozwiązuje.** Model, który przed sekundą przekroczył limit czasu, przy
następnym żądaniu NADAL był pierwszym kandydatem. Każde kolejne żądanie płaciło więc pełny
limit czasu, zanim spadło na fallback — a limity bywają liczone w dziesiątkach sekund. Przy
padniętym modelu głównym cała platforma zwalniała o tę wartość przy KAŻDYM zapytaniu, i to
w sposób dla użytkownika niewytłumaczalny: odpowiedzi przychodziły, tylko bardzo wolno.

**Dlaczego ODSUNIĘCIE, a nie wykluczenie.** Kandydat z otwartym wyłącznikiem trafia na
KONIEC listy, nie znika z niej. Różnica jest istotna w przypadku, który zdarza się najczęściej
przy awarii: gdy padło wszystko (sieć, wspólny host silników), wykluczanie zostawiłoby pustą
listę kandydatów i ``NoModelAvailableError`` — czyli twardą odmowę zamiast próby, która
mogłaby się powieść. Odsunięcie zachowuje własność „spróbuj mimo wszystko, ale na końcu".

**Co liczy się jako awaria — i co świadomie NIE.** Wyłącznie błąd backendu przy realnym
wywołaniu (``ModelBackendError``: limit czasu, brak połączenia, błąd silnika). NIE liczą się
pominięcia wynikające z WŁAŚCIWOŚCI ŻĄDANIA: brak wizji przy obrazie, prompt niemieszczący
się w oknie, blokada egress. Model pominięty, bo prompt był za długi, jest w pełni zdrowy —
karanie go zdegradowałoby go za cudzy błąd i przy następnym, krótszym żądaniu wysłałoby ruch
w gorsze miejsce.

**Licznik jest KOLEJNYCH awarii, nie sumy.** Pojedynczy sukces zeruje go w całości. Model,
który działa z przerwami, nie ma się więc dogrywać do wyłączenia przez tydzień drobnych
potknięć — wyłącznik ma łapać awarię trwającą TERAZ, a nie prowadzić statystykę.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(slots=True)
class _Stan:
    """Stan jednego modelu: ile kolejnych awarii i kiedy była ostatnia."""

    kolejnych_awarii: int = 0
    ostatnia_awaria: float = 0.0


@dataclass(slots=True)
class RejestrZdrowia:
    """Licznik świeżych awarii per model i czasowe odsunięcie kandydata.

    Args:
        awarii_do_otwarcia: Ile KOLEJNYCH awarii otwiera wyłącznik.
        odsuniecie_sekund: Jak długo model pozostaje odsunięty po otwarciu.
        zegar: Wstrzykiwalny zegar (``time.monotonic``), żeby testy były deterministyczne.
    """

    awarii_do_otwarcia: int
    odsuniecie_sekund: float
    zegar: Callable[[], float] = time.monotonic
    # Klucz to identyfikator modelu, więc mapa jest ograniczona rozmiarem rejestru —
    # nie rośnie z ruchem i nie wymaga sprzątania.
    _stany: dict[str, _Stan] = field(default_factory=dict)
    # Endpointy FastAPI biegną w puli wątków, więc licznik jest modyfikowany współbieżnie.
    _zamek: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def odnotuj_awarie(self, model_id: str) -> None:
        """Zwiększa licznik kolejnych awarii modelu.

        Args:
            model_id: Identyfikator modelu, który zawiódł przy realnym wywołaniu.
        """
        with self._zamek:
            stan = self._stany.setdefault(model_id, _Stan())
            stan.kolejnych_awarii += 1
            stan.ostatnia_awaria = self.zegar()

    def odnotuj_sukces(self, model_id: str) -> None:
        """Zeruje licznik — model odpowiedział, więc awaria się skończyła.

        Args:
            model_id: Identyfikator modelu, który odpowiedział poprawnie.
        """
        with self._zamek:
            self._stany.pop(model_id, None)

    def odsuniety(self, model_id: str) -> bool:
        """Czy wyłącznik tego modelu jest OTWARTY w tej chwili.

        Args:
            model_id: Identyfikator modelu.

        Returns:
            ``True``, gdy model przekroczył próg awarii i nie minął jeszcze czas odsunięcia.
        """
        with self._zamek:
            stan = self._stany.get(model_id)
            if stan is None or stan.kolejnych_awarii < self.awarii_do_otwarcia:
                return False
            return (self.zegar() - stan.ostatnia_awaria) < self.odsuniecie_sekund

    def uporzadkuj(self, kandydaci: list[str]) -> list[str]:
        """Przesuwa modele z otwartym wyłącznikiem na KONIEC, zachowując resztę kolejności.

        Podział jest STABILNY: wewnątrz obu grup kolejność pozostaje ta, którą ustaliły
        reguły wyboru (``select_candidates``). Wyłącznik ma odsuwać niedziałające modele,
        a nie przestawiać polityki routingu.

        Args:
            kandydaci: Lista identyfikatorów w kolejności ustalonej przez router.

        Returns:
            Ta sama lista z odsuniętymi modelami na końcu.
        """
        zdrowe = [m for m in kandydaci if not self.odsuniety(m)]
        odsuniete = [m for m in kandydaci if self.odsuniety(m)]
        return zdrowe + odsuniete
