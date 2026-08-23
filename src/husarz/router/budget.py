"""Budżet okna kontekstu — czy prompt razem z odpowiedzią zmieści się u danego modelu.

**Po co ten moduł istnieje.** Router clampował dotąd wyłącznie ``max_tokens`` wg kontroli
kosztów, ale nikt nie sprawdzał, czy sam PROMPT mieści się w oknie modelu. Przy modelu 7B
i pętli narzędziowej to realny problem, zaobserwowany w tym projekcie: rozmowa rośnie
o wyniki narzędzi (JSON, gęsty tokenowo), przekracza okno, backend zwraca błąd albo ucina
kontekst po cichu, a agent wypala limit iteracji, nie wiedząc dlaczego.

**Dlaczego oszacowanie, a nie dokładny pomiar.** Dokładne policzenie tokenów wymaga
tokenizera KONKRETNEGO modelu, a rdzeń Husarza ma pięć zależności runtime i żadna nim nie
jest. Dokładanie ``transformers``/``tiktoken`` do rdzenia tylko po to, by policzyć długość,
byłoby złą wymianą. Szacujemy więc — ale zachowawczo i na podstawie POMIARU, nie intuicji.

**Kalibracja (pomiar, nie szacunek).** Zmierzone 2026-08-23 na `qwen2.5-coder:7b` przez
Ollamę (`prompt_eval_count`, czyli licznik realnego tokenizera):

| Rodzaj treści | znaków na token |
|---|---|
| polski, proza | 2,19 |
| polski, techniczny | 2,08 |
| kod (Python) | 2,88 |
| angielski | 2,70 |
| JSON / wpis dziennika | **1,68** |

Najgęstszy okazał się JSON — i to nie przypadek, tylko najgorszy przypadek dla NAS: wyniki
narzędzi w pętli agentowej są właśnie JSON-em. Dlatego dzielnik bierzemy z tego wiersza.

Narzut samego szablonu czatu (znaczniki ról) zmierzony tak samo: **29 tokenów** przy jednej
pustej wiadomości i **~3 tokeny** na każdą kolejną.

**Skutek: szacujemy Z GÓRY.** Dla typowej polskiej rozmowy oszacowanie jest o ~30% wyższe od
prawdy, więc odmówimy nieco wcześniej, niż musielibyśmy. To świadomy wybór: fałszywa odmowa
z czytelnym komunikatem jest tańsza niż cicha awaria backendu w środku pętli narzędziowej.
Model o większym oknie i tak zostanie wypróbowany, bo router traktuje niezmieszczenie się jak
każdą inną przyczynę pominięcia kandydata.

**Czego to oszacowanie NIE obejmuje.** Obrazów — modele wizyjne liczą je osobno i zależnie od
rozdzielczości, a my nie mamy jak tego odtworzyć bez tokenizera modelu. Prompt z obrazami
będzie więc niedoszacowany; ograniczenie zapisane w `docs/ROUTER.md`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from husarz.router.types import ChatMessage

# Znaki na token — z NAJGĘSTSZEGO zmierzonego wiersza (JSON, 1,68). Zaokrąglone w dół do 1,6,
# żeby zostawić margines na treść jeszcze gęstszą (base64, zbite logi).
_ZNAKOW_NA_TOKEN = 1.6

# Stały narzut szablonu czatu. Zmierzone 29 — bierzemy 32.
_NARZUT_STALY = 32

# Narzut na każdą wiadomość (znaczniki roli). Zmierzone ~3 — bierzemy 6.
_NARZUT_NA_WIADOMOSC = 6

# Ile tokenów zarezerwować na ODPOWIEDŹ, gdy ani żądanie, ani model nie podają `max_tokens`.
# Odpowiedź krótsza niż to jest w praktyce bezużyteczna, a rezerwa zerowa oznaczałaby zgodę
# na prompt wypełniający okno co do tokena — czyli na model, który nie ma czym odpowiedzieć.
_DOMYSLNA_REZERWA = 512


def estimate_prompt_tokens(messages: Sequence[ChatMessage]) -> int:
    """Zachowawczo szacuje liczbę tokenów promptu.

    Args:
        messages: Wiadomości żądania.

    Returns:
        Oszacowanie Z GÓRY — patrz kalibracja w dokumentacji modułu. Obrazy NIE są liczone.
    """
    znaki = sum(len(m.content) for m in messages)
    return (
        _NARZUT_STALY + _NARZUT_NA_WIADOMOSC * len(messages) + math.ceil(znaki / _ZNAKOW_NA_TOKEN)
    )


def reserve_for_reply(request_max_tokens: int | None, model_max_tokens: int | None) -> int:
    """Ile tokenów trzeba zostawić na odpowiedź.

    Kolejność źródeł jest istotna: najpierw to, o co poprosił wołający, potem domyślna wartość
    modelu, a dopiero na końcu wartość zapasowa. Odwrotna kolejność ignorowałaby jawne żądanie.

    Args:
        request_max_tokens: ``max_tokens`` z żądania (już po kontroli kosztów) albo ``None``.
        model_max_tokens: ``max_tokens`` z rejestru modelu albo ``None``.

    Returns:
        Liczba tokenów do zarezerwowania.
    """
    if request_max_tokens is not None:
        return request_max_tokens
    if model_max_tokens is not None:
        return model_max_tokens
    return _DOMYSLNA_REZERWA


def check_fits(
    messages: Sequence[ChatMessage],
    *,
    context_length: int,
    request_max_tokens: int | None,
    model_max_tokens: int | None,
) -> str | None:
    """Sprawdza, czy prompt WRAZ Z rezerwą na odpowiedź mieści się w oknie modelu.

    Zwraca POWÓD niezmieszczenia zamiast rzucać wyjątkiem, bo wołający (router) traktuje to
    jak każdą inną przyczynę pominięcia kandydata i próbuje modelu następnego. Wyjątek
    przerwałby łańcuch fallbacków, choć model o większym oknie mógłby żądanie obsłużyć.

    Args:
        messages: Wiadomości żądania.
        context_length: Okno kontekstu modelu (z rejestru).
        request_max_tokens: ``max_tokens`` z żądania albo ``None``.
        model_max_tokens: ``max_tokens`` z rejestru modelu albo ``None``.

    Returns:
        ``None``, gdy się mieści; inaczej czytelny po polsku powód z liczbami.
    """
    prompt = estimate_prompt_tokens(messages)
    rezerwa = reserve_for_reply(request_max_tokens, model_max_tokens)
    potrzeba = prompt + rezerwa
    if potrzeba <= context_length:
        return None
    return (
        f"prompt nie mieści się w oknie kontekstu: szacowane {prompt} tok. + rezerwa na "
        f"odpowiedź {rezerwa} tok. = {potrzeba} > {context_length} (context_length modelu). "
        f"Skróć rozmowę, zmniejsz max_tokens albo użyj modelu z większym oknem. "
        f"Oszacowanie jest ZACHOWAWCZE (liczy z góry) — patrz husarz.router.budget."
    )
