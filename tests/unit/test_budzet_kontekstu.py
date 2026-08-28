"""Budżet okna kontekstu — estymator i bramka w routerze (Etap 16).

Estymator jest ZACHOWAWCZY z założenia: bez tokenizera modelu nie da się policzyć dokładnie,
więc liczymy z góry. Te testy pilnują dwóch rzeczy naraz — że nie zaniża (bo wtedy bramka
przepuszcza prompt, który się nie zmieści) i że nie zawyża absurdalnie (bo wtedy odmawia
promptom, które by weszły).

Punktem odniesienia są POMIARY realnym tokenizerem (`qwen2.5-coder:7b` przez Ollamę,
`prompt_eval_count`), wykonane przy pisaniu modułu i zapisane w jego dokumentacji. Testy nie
wymagają Ollamy — liczby są wpisane jako dane.
"""

from __future__ import annotations

import pytest

from husarz.router.budget import (
    check_fits,
    estimate_prompt_tokens,
    reserve_for_reply,
)
from husarz.router.types import ChatMessage

pytestmark = pytest.mark.unit


# Pomiary z 2026-08-23, `qwen2.5-coder:7b`, licznik `prompt_eval_count`.
# (opis, tekst, ile tokenów naliczył REALNY tokenizer)
_POMIARY: list[tuple[str, str, int]] = [
    (
        "polski proza",
        "Husarz to suwerenna, samodzielnie hostowana platforma wieloagentowa. "
        "Zasadą nadrzędną jest suwerenność danych: modele i dane nie opuszczają infrastruktury "
        "użytkownika bez wyraźnej zgody operatora. Domyślnie obowiązuje deny-all egress.",
        107,
    ),
    (
        "json/log",
        '{"timestamp":"2026-08-23T01:00:00+00:00","actor":"api",'
        '"action":"git.connection.add","detail":{"name":"moj-github","provider":"github"}}',
        81,
    ),
]


def _wiadomosc(tresc: str) -> ChatMessage:
    return ChatMessage(role="user", content=tresc)


@pytest.mark.parametrize(("opis", "tekst", "realnie"), _POMIARY)
def test_estymator_nie_zanizza_wobec_realnego_tokenizera(
    opis: str, tekst: str, realnie: int
) -> None:
    """Najważniejszy niezmiennik: NIGDY nie oszacuj mniej, niż naliczy tokenizer.

    Zaniżenie oznacza, że bramka przepuszcza prompt, który się nie zmieści — czyli dokładnie
    tę awarię, której ma zapobiegać.
    """
    oszacowane = estimate_prompt_tokens([_wiadomosc(tekst)])

    assert oszacowane >= realnie, f"{opis}: oszacowano {oszacowane} < zmierzone {realnie}"


@pytest.mark.parametrize(("opis", "tekst", "realnie"), _POMIARY)
def test_estymator_nie_zawyza_absurdalnie(opis: str, tekst: str, realnie: int) -> None:
    """Zawyżenie też ma koszt: odmawiamy promptom, które by weszły.

    Granica 2,5× jest arbitralna, ale nie dowolna — przy zapasie większym niż ten estymator
    odcinałby ponad połowę użytecznego okna, co zamieniłoby zabezpieczenie w przeszkodę.
    """
    oszacowane = estimate_prompt_tokens([_wiadomosc(tekst)])

    assert oszacowane <= realnie * 2.5, f"{opis}: oszacowano {oszacowane}, zmierzone {realnie}"


def test_narzut_szablonu_jest_uwzgledniony() -> None:
    """Pusta wiadomość to nie zero tokenów — sam szablon czatu kosztuje (zmierzone: 29)."""
    assert estimate_prompt_tokens([_wiadomosc("")]) >= 29


def test_kazda_wiadomosc_dokłada_narzut() -> None:
    """Rozmowa z wielu krótkich wiadomości kosztuje więcej niż ta sama treść w jednej."""
    jedna = estimate_prompt_tokens([_wiadomosc("abc" * 10)])
    wiele = estimate_prompt_tokens([_wiadomosc("abc") for _ in range(10)])

    assert wiele > jedna


def test_rezerwa_respektuje_kolejnosc_zrodel() -> None:
    """Żądanie ma pierwszeństwo przed modelem, model przed wartością zapasową."""
    assert reserve_for_reply(100, 900) == 100
    assert reserve_for_reply(None, 900) == 900
    assert reserve_for_reply(None, None) == 512


def test_prompt_ktory_sie_miesci_przechodzi() -> None:
    """Nośność: bramka nie może odrzucać zwykłych, krótkich rozmów."""
    powod = check_fits(
        [_wiadomosc("Krótkie pytanie.")],
        context_length=8192,
        request_max_tokens=256,
        model_max_tokens=None,
    )

    assert powod is None


def test_prompt_ktory_sie_nie_miesci_daje_czytelny_powod() -> None:
    """Komunikat musi nieść LICZBY i podpowiedź, co zrobić — inaczej nikt go nie użyje."""
    powod = check_fits(
        [_wiadomosc("x" * 40_000)],
        context_length=8192,
        request_max_tokens=512,
        model_max_tokens=None,
    )

    assert powod is not None
    assert "8192" in powod
    assert "context_length" in powod
    assert "max_tokens" in powod


def test_rezerwa_na_odpowiedz_jest_wliczana() -> None:
    """Regresja: prompt mieszczący się CO DO TOKENA nie zostawia modelowi czym odpowiedzieć.

    Sprawdzamy przypadek graniczny — ten sam prompt przechodzi przy małej rezerwie i odpada
    przy dużej. Bez wliczania rezerwy oba dawałyby ten sam wynik.
    """
    wiadomosci = [_wiadomosc("y" * 8_000)]

    mala = check_fits(wiadomosci, context_length=6000, request_max_tokens=16, model_max_tokens=None)
    duza = check_fits(
        wiadomosci, context_length=6000, request_max_tokens=2000, model_max_tokens=None
    )

    assert mala is None, "prompt sam w sobie powinien się zmieścić"
    assert duza is not None, "z dużą rezerwą na odpowiedź nie powinien"


# --------------------------------------------------- obrazy: koniec cichej niedoszacówki


def test_obraz_KOSZTUJE_w_oszacowaniu() -> None:
    """Regresja: obrazy były liczone na ZERO tokenów.

    `ChatMessage.images` idzie do modelu wizyjnego tak samo jak treść, a bramka budżetu
    udawała, że nic nie waży. Skutek: dla żądania z obrazem meldowała „mieści się", choć
    model odrzuci je albo po cichu utnie kontekst — czyli bramka zawodziła dokładnie
    w przypadku, dla którego istnieje.
    """
    from husarz.router.types import ImagePart

    bez = ChatMessage("user", "Co jest na obrazku?")
    z_obrazem = ChatMessage(
        "user", "Co jest na obrazku?", images=[ImagePart(mime="image/png", data_b64="x")]
    )

    assert estimate_prompt_tokens([z_obrazem]) > estimate_prompt_tokens([bez])
    # Bez tej asercji test przeszedłby także dla kosztu równego jednemu tokenowi, czyli dla
    # zaokrąglenia, które w praktyce nadal jest zerem.
    assert estimate_prompt_tokens([z_obrazem]) - estimate_prompt_tokens([bez]) > 1000


def test_kazdy_obraz_liczony_OSOBNO() -> None:
    """Dwa obrazy kosztują dwa razy tyle — inaczej galeria byłaby znów niedoszacowana."""
    from husarz.router.types import ImagePart

    obraz = ImagePart(mime="image/png", data_b64="x")
    jeden = ChatMessage("user", "", images=[obraz])
    trzy = ChatMessage("user", "", images=[obraz, obraz, obraz])

    roznica = estimate_prompt_tokens([trzy]) - estimate_prompt_tokens([jeden])
    na_obraz = estimate_prompt_tokens([jeden]) - estimate_prompt_tokens([ChatMessage("user", "")])
    assert roznica == 2 * na_obraz


def test_obraz_w_MALYM_oknie_powoduje_pominiecie_kandydata() -> None:
    """Skutek, dla którego ta poprawka istnieje — sprawdzany na bramce, nie na estymatorze.

    Model wizyjny o oknie 2048 nie obsłuży żądania z obrazem, a przed poprawką dostałby je
    jako „mieszczące się".
    """
    from husarz.router.types import ImagePart

    wiadomosci = [ChatMessage("user", "Opisz.", images=[ImagePart(mime="image/png", data_b64="x")])]

    powod = check_fits(
        wiadomosci, context_length=2048, request_max_tokens=256, model_max_tokens=None
    )

    assert powod is not None, "żądanie z obrazem uznane za mieszczące się w oknie 2048"
    assert "2048" in powod


def test_obraz_w_DUZYM_oknie_nadal_przechodzi() -> None:
    """Nośność: stały koszt obrazu nie może blokować modeli, które go obsłużą."""
    from husarz.router.types import ImagePart

    wiadomosci = [ChatMessage("user", "Opisz.", images=[ImagePart(mime="image/png", data_b64="x")])]

    assert (
        check_fits(wiadomosci, context_length=32768, request_max_tokens=256, model_max_tokens=None)
        is None
    )
