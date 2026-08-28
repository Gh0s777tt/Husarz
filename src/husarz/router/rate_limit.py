"""Prosty ogranicznik żądań (token bucket) — kontrola kosztów.

Zegar jest wstrzykiwalny (``now_fn``), więc test jest w pełni deterministyczny.
Domyślnie używamy ``time.monotonic`` (odporny na zmiany zegara systemowego).
"""

from __future__ import annotations

import time
from collections.abc import Callable

from husarz.router.errors import RateLimitExceededError


class RateLimiter:
    """Token bucket o pojemności ``max_per_minute`` i uzupełnianiu w tempie/min."""

    def __init__(
        self,
        max_per_minute: int,
        *,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_per_minute <= 0:
            raise ValueError("max_per_minute musi być dodatnie.")
        self._capacity = float(max_per_minute)
        self._tokens = float(max_per_minute)
        self._refill_per_second = max_per_minute / 60.0
        self._now = now_fn
        self._last = now_fn()

    @property
    def pojemnosc(self) -> float:
        """Pojemność kubełka (liczba żądań na minutę) — do komunikatów."""
        return self._capacity

    def acquire(self) -> None:
        """Pobiera jeden token lub rzuca ``RateLimitExceededError``."""
        now = self._now()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._refill_per_second)
        if self._tokens < 1.0:
            raise RateLimitExceededError(
                f"Przekroczono limit {int(self._capacity)} żądań/min (kontrola kosztów)."
            )
        self._tokens -= 1.0


class RateLimiterPerPrincipal:
    """Limit DWUPOZIOMOWY: osobny kubełek wywołującego oraz wspólny kubełek instalacji.

    **Po co dwa poziomy.** Sam limit globalny chroni instalację i silniki, do których
    Husarz się odzywa, ale nie chroni użytkowników PRZED SOBĄ: jedno konto odpytujące
    w pętli potrafiło odebrać diagnozę operatorowi dokładnie w trakcie awarii — czyli
    wtedy, gdy jest ona potrzebna. Sam limit per wywołujący z kolei niczego nie chroni,
    gdy kont jest wiele: dziesięć kont po sześć żądań to nadal sześćdziesiąt zapytań do
    silników. Dopiero oba naraz dają obie własności.

    **Kolejność ma znaczenie i nie jest kosmetyczna.** Najpierw kubełek wywołującego,
    potem globalny. Dzięki temu żądania konta, które już przekroczyło swój przydział,
    NIE zjadają tokenów wspólnych — inaczej nadużywające konto odbierałoby budżet reszcie
    mimo własnych odmów, czyli mechanizm broniłby wyłącznie na papierze.

    **Ograniczenie pamięci.** Mapa wywołujący → kubełek rośnie z liczbą wywołujących,
    a etykieta pochodzi z uwierzytelnienia, więc przy wyłączonym uwierzytelnianiu jest
    pusta i mapa ma jeden wpis. Mimo to trzymamy twardy limit rozmiaru: kubełek PEŁNY
    (odtworzony do pojemności) niczego nie pamięta, więc można go usunąć bez zmiany
    zachowania. Gdy pełnych brakuje, usuwamy najdawniej używany — to osłabia limit dla
    jednego wywołującego, ale nie pozwala mapie rosnąć bez końca.

    Args:
        max_per_minute: Limit globalny instalacji.
        per_principal: Limit dla POJEDYNCZEGO wywołującego. ``None`` wyłącza ten poziom.
        max_principals: Górna granica liczby pamiętanych kubełków.
        now_fn: Wstrzykiwalny zegar (testy deterministyczne).

    Raises:
        ValueError: Gdy ``per_principal`` nie jest mniejszy od limitu globalnego — taki
            kubełek nigdy nie zadziałałby jako pierwszy, więc byłby polem bez skutku.
    """

    def __init__(
        self,
        max_per_minute: int,
        *,
        per_principal: int | None = None,
        max_principals: int = 1024,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        if per_principal is not None and per_principal >= max_per_minute:
            raise ValueError(
                f"Limit per wywołujący ({per_principal}) musi być MNIEJSZY od globalnego "
                f"({max_per_minute}). Przy wartości równej albo większej kubełek globalny "
                f"wyczerpywałby się pierwszy, więc ten per wywołujący nie zmieniałby nic."
            )
        self._globalny = RateLimiter(max_per_minute, now_fn=now_fn)
        self._per_principal = per_principal
        self._max_principals = max_principals
        self._now = now_fn
        self._kubelki: dict[str, RateLimiter] = {}
        self._ostatnie_uzycie: dict[str, float] = {}

    def acquire(self, principal: str = "") -> None:
        """Pobiera token z kubełka wywołującego, a potem z globalnego.

        **Pusta etykieta pomija poziom per wywołujący.** Etykieta jest pusta wyłącznie
        wtedy, gdy uwierzytelnianie jest WYŁĄCZONE — a wtedy wszyscy wywołujący są dla
        systemu jedną osobą i kubełek per wywołujący nie daje ŻADNEJ izolacji. Dawałby
        natomiast realną stratę: obniżałby efektywny limit instalacji jednoosobowej
        z globalnego do wartości per osobę (u nas z 6 na 3 na minutę). Wykryte
        uruchomieniem, nie rozumowaniem — sześć kolejnych wywołań na loopbacku dawało
        `200 200 200 429 429 429` zamiast sześciu odpowiedzi.

        Args:
            principal: Referencja wywołującego (``user:...``/``token:...``); pusta przy
                wyłączonym uwierzytelnianiu.

        Raises:
            RateLimitExceededError: Gdy któryś z limitów jest wyczerpany. Komunikat mówi
                KTÓRY, bo dla wywołującego to różnica między „poczekaj" a „to nie ty".
        """
        if self._per_principal is not None and principal:
            try:
                self._kubelek(principal).acquire()
            except RateLimitExceededError as exc:
                raise RateLimitExceededError(
                    f"Przekroczono TWÓJ limit {self._per_principal} żądań/min. "
                    f"Limit instalacji ({int(self._globalny.pojemnosc)}/min) pozostaje "
                    f"nienaruszony dla pozostałych — to celowe."
                ) from exc
            self._ostatnie_uzycie[principal] = self._now()
        try:
            self._globalny.acquire()
        except RateLimitExceededError as exc:
            raise RateLimitExceededError(
                f"Przekroczono limit INSTALACJI {int(self._globalny.pojemnosc)} żądań/min "
                f"(nie Twój własny). Ktoś inny również odpytuje."
            ) from exc

    def _kubelek(self, principal: str) -> RateLimiter:
        """Zwraca kubełek wywołującego, tworząc go i sprzątając mapę w razie potrzeby."""
        istniejacy = self._kubelki.get(principal)
        if istniejacy is not None:
            return istniejacy
        if len(self._kubelki) >= self._max_principals:
            self._zwolnij_miejsce()
        assert self._per_principal is not None  # gwarantowane przez wołającego
        nowy = RateLimiter(self._per_principal, now_fn=self._now)
        self._kubelki[principal] = nowy
        return nowy

    def _zwolnij_miejsce(self) -> None:
        """Usuwa jeden kubełek: najpierw NAJDAWNIEJ używany (najbliższy pełnemu).

        Kubełek odtwarza się w tempie ``per_principal`` na minutę, więc najdawniej używany
        jest zarazem najpełniejszy — jego usunięcie zmienia zachowanie najmniej. Przy
        pustej mapie nie ma czego usuwać (nie zdarza się, bo wołamy po sprawdzeniu rozmiaru).
        """
        if not self._ostatnie_uzycie:
            self._kubelki.clear()
            return
        najstarszy = min(self._ostatnie_uzycie, key=lambda k: self._ostatnie_uzycie[k])
        self._kubelki.pop(najstarszy, None)
        self._ostatnie_uzycie.pop(najstarszy, None)
