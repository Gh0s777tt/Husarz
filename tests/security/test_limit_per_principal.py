"""Kubełek limitu tempa PER WYWOŁUJĄCY — warunek wstępny rozszerzenia `diagnostics:read`.

**Skąd ta pozycja.** Panel sędziowski oceniający rozszerzenie uprawnienia `diagnostics:read`
poza administratora dał rozszerzeniu 4,7/10 i uznał je za NIEBEZPIECZNE, wskazując konkretną
przyczynę: limit tempa był jeden na całą instalację. Konto odpytujące w pętli potrafiło więc
odebrać diagnozę operatorowi — dokładnie w trakcie awarii, czyli wtedy, gdy jest ona
potrzebna. ROADMAP zapisała kubełek per wywołujący jako TWARDY WARUNEK WSTĘPNY.

Sedno jest w jednym teście: `test_konto_odpytujace_w_petli_NIE_odbiera_dostepu_operatorowi`.
Reszta pilnuje, żeby mechanizm nie okazał się ozdobą — bo limit per wywołujący ustawiony
wyżej niż globalny nie zadziałałby nigdy (kubełek globalny wyczerpałby się pierwszy),
a pole wyglądające na działające i niedziałające jest gorsze od jego braku.
"""

from __future__ import annotations

import pytest

from husarz.config.schema import DiagnosticsConfig
from husarz.router.errors import RateLimitExceededError
from husarz.router.rate_limit import RateLimiterPerPrincipal

pytestmark = pytest.mark.security


class _Zegar:
    """Wstrzykiwalny zegar — testy tempa muszą być deterministyczne."""

    def __init__(self) -> None:
        self.teraz = 0.0

    def __call__(self) -> float:
        return self.teraz


def test_konto_odpytujace_w_petli_NIE_odbiera_dostepu_operatorowi() -> None:
    """Właściwość, dla której cała ta pozycja istnieje."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(6, per_principal=3, now_fn=zegar)

    for _ in range(3):
        limit.acquire("user:natretny")
    with pytest.raises(RateLimitExceededError):
        limit.acquire("user:natretny")

    # Operator ma nadal swój przydział — i to jest cała różnica wobec stanu sprzed zmiany.
    limit.acquire("user:operator")
    limit.acquire("user:operator")
    limit.acquire("user:operator")


def test_limit_INSTALACJI_nadal_obowiazuje() -> None:
    """Bez tej asercji kubełki per osobę byłyby furtką: dziesięć kont po sześć żądań."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(4, per_principal=3, now_fn=zegar)

    limit.acquire("user:a")
    limit.acquire("user:a")
    limit.acquire("user:b")
    limit.acquire("user:b")

    # Globalny wyczerpany, choć `user:c` nie zużył ani jednego własnego tokenu.
    with pytest.raises(RateLimitExceededError, match="INSTALACJI"):
        limit.acquire("user:c")


def test_odmowa_MOWI_ktory_limit_zadzialal() -> None:
    """Dla wywołującego to różnica między „poczekaj" a „to nie ty"."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(6, per_principal=2, now_fn=zegar)
    limit.acquire("user:a")
    limit.acquire("user:a")

    with pytest.raises(RateLimitExceededError, match="TWÓJ limit"):
        limit.acquire("user:a")


def test_odmowa_wlasnego_kubelka_NIE_zjada_tokenow_wspolnych() -> None:
    """Kolejność sprawdzania jest mechanizmem, nie kosmetyką.

    Gdyby kubełek globalny szedł pierwszy, konto ponad swoim przydziałem zjadałoby budżet
    reszcie mimo własnych odmów — czyli mechanizm broniłby wyłącznie na papierze.
    """
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(6, per_principal=2, now_fn=zegar)
    for _ in range(2):
        limit.acquire("user:natretny")
    for _ in range(10):
        with pytest.raises(RateLimitExceededError):
            limit.acquire("user:natretny")

    # Zostały DOKŁADNIE 4 tokeny globalne (6 - 2): dziesięć odmów natrętnego konta nie
    # zabrało ani jednego. Rozdzielamy je na dwa konta, bo każde ma własny przydział 2.
    for konto in ("user:operator", "user:audytor"):
        limit.acquire(konto)
        limit.acquire(konto)
    with pytest.raises(RateLimitExceededError, match="INSTALACJI"):
        limit.acquire("user:ktos-trzeci")


def test_kubelki_odtwarzaja_sie_z_czasem() -> None:
    """Limit ma spowalniać, a nie blokować na stałe."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(6, per_principal=3, now_fn=zegar)
    for _ in range(3):
        limit.acquire("user:a")
    with pytest.raises(RateLimitExceededError):
        limit.acquire("user:a")

    zegar.teraz += 60.0

    limit.acquire("user:a")


def test_mapa_kubelkow_ma_TWARDY_limit_rozmiaru() -> None:
    """Etykieta wywołującego pochodzi z uwierzytelnienia, ale pamięć i tak musi być ograniczona."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(1000, per_principal=2, max_principals=8, now_fn=zegar)

    for i in range(200):
        zegar.teraz += 0.01
        limit.acquire(f"user:{i}")

    assert len(limit._kubelki) <= 8


def test_limit_per_osobe_rowny_globalnemu_jest_ODRZUCANY() -> None:
    """Taki kubełek nie zadziałałby nigdy — to byłoby pole bez skutku."""
    with pytest.raises(ValueError, match="MNIEJSZY"):
        RateLimiterPerPrincipal(6, per_principal=6)
    with pytest.raises(ValueError, match="MNIEJSZE"):
        DiagnosticsConfig(max_requests_per_minute=6, max_requests_per_minute_per_principal=6)


def test_wylaczony_poziom_per_osobe_zachowuje_stare_zachowanie() -> None:
    """Bez tej asercji nie wiadomo, czy `None` naprawdę wyłącza, czy tylko wygląda."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(3, per_principal=None, now_fn=zegar)

    limit.acquire("user:a")
    limit.acquire("user:b")
    limit.acquire("user:c")

    # Wspólny kubełek wyczerpany — nikt nie ma własnego przydziału.
    with pytest.raises(RateLimitExceededError):
        limit.acquire("user:d")
    assert limit._kubelki == {}


def test_wylaczone_uwierzytelnianie_POMIJA_poziom_per_osobe() -> None:
    """Bez uwierzytelnienia kubełek per osobę nie daje izolacji, a odbiera przepustowość.

    Wszyscy wywołujący są wtedy dla systemu jedną osobą, więc poziom per wywołujący nie ma
    kogo od kogo oddzielić — obniżałby tylko efektywny limit instalacji jednoosobowej
    z globalnego do wartości per osobę. Wykryte uruchomieniem na żywo: sześć wywołań
    `GET /api/doctor` na loopbacku dawało `200 200 200 429 429 429` zamiast sześciu
    odpowiedzi, mimo limitu globalnego 6/min.
    """
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(6, per_principal=2, now_fn=zegar)

    for _ in range(6):
        limit.acquire("")

    # Dopiero limit INSTALACJI zatrzymuje siódme żądanie.
    with pytest.raises(RateLimitExceededError, match="INSTALACJI"):
        limit.acquire("")
    assert limit._kubelki == {}, "dla pustej etykiety nie zakładamy kubełka"


def test_etykieta_NIEpusta_nadal_dostaje_wlasny_kubelek() -> None:
    """Bez tej asercji test wyżej przechodziłby też, gdyby poziom per osobę wyłączono zupełnie."""
    zegar = _Zegar()
    limit = RateLimiterPerPrincipal(6, per_principal=2, now_fn=zegar)

    limit.acquire("user:a")
    limit.acquire("user:a")

    with pytest.raises(RateLimitExceededError, match="TWÓJ limit"):
        limit.acquire("user:a")
