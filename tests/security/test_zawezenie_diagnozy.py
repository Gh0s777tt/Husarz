"""Zawężanie treści diagnozy w odpowiedzi HTTP (`GET /api/doctor`).

**Skąd ta pozycja.** Panel z Etapu 17l odrzucił rozszerzenie uprawnienia `diagnostics:read`
poza administratora, a jednym z powodów było ujawnianie aktualnej TOPOLOGII instalacji —
na ścieżce szczęśliwej, nie tylko przy awarii. ROADMAP zapisała zawężenie ładunku jako krok
obniżający stawkę tamtej dyskusji.

**Zakres okazał się węższy, niż zakładał zapis, i to jest odnotowane.** ROADMAP mówiła
o „pełnych endpointach i ścieżkach bezwzględnych". Pomiar na dostarczonej konfiguracji
pokazał, że ścieżek bezwzględnych diagnoza nie emituje wcale — pojawiają się WYŁĄCZNIE
w kontroli katalogów zapisywalnych i tylko wtedy, gdy operator skonfiguruje je bezwzględnie.

Czego zawężenie NIE robi i robić nie może: nie ukrywa hosta ani portu. Bez nich ustalenie
„silnik nie odpowiedział" nie mówi, KTÓRY silnik — a diagnoza, z której nie wynika, co
naprawić, nie jest diagnozą.
"""

from __future__ import annotations

import pytest

from husarz.api.redakcja import zawez_dla_http

pytestmark = pytest.mark.security


@pytest.mark.parametrize(
    ("wejscie", "oczekiwane"),
    [
        (
            "Silnik pod http://localhost:8000/v1 nie odpowiedział.",
            "Silnik pod http://localhost:8000 nie odpowiedział.",
        ),
        (
            "Endpoint https://gpu-01.wewn.firma.pl:8443/v1/chat/completions jest nieosiągalny.",
            "Endpoint https://gpu-01.wewn.firma.pl:8443 jest nieosiągalny.",
        ),
        (
            "Nadaj prawa zapisu do /var/lib/husarz/audit albo wskaż inny katalog.",
            "Nadaj prawa zapisu do …/audit albo wskaż inny katalog.",
        ),
        (
            "Sprawdź uprawnienia do /home/operator/dane-firmowe/husarz/workspace.",
            "Sprawdź uprawnienia do …/workspace.",
        ),
        (
            "Sprawdź ręcznie uprawnienia do C:\\ProgramData\\Husarz\\audit.",
            "Sprawdź ręcznie uprawnienia do …\\audit.",
        ),
    ],
)
def test_zawezenie_skraca_adresy_i_sciezki(wejscie: str, oczekiwane: str) -> None:
    """Adres traci część ścieżkową, ścieżka bezwzględna — wszystko poza ostatnim segmentem."""
    assert zawez_dla_http(wejscie) == oczekiwane


@pytest.mark.parametrize(
    "tekst",
    [
        "Zmień pole `model` w config/models.yaml na jeden z dostępnych.",
        "Przygotuj model wg ollama/README.md (`ollama create ...`).",
        "Silnik odpowiada, ale NIE MA modelu 'bielik-11b-v3.0-instruct' (agent bielik).",
        "Uruchom Husarza na innym porcie (`--port`) albo popraw endpoint modelu.",
        "Dostępne: husarz:latest, qwen2.5-coder:7b.",
    ],
)
def test_zawezenie_NIE_rusza_tresci_uzytecznej(tekst: str) -> None:
    """Nadgorliwe cięcie odebrałoby diagnozie sens.

    Ścieżki w rodzaju `config/models.yaml` są konwencją PROJEKTU, identyczną w każdej
    instalacji — nie mówią o operatorze nic i muszą zostać, bo wskazują plik do poprawienia.
    """
    assert zawez_dla_http(tekst) == tekst


def test_host_i_port_ZOSTAJA() -> None:
    """Granica zawężania: bez nich nie wiadomo, którego silnika dotyczy ustalenie."""
    wynik = zawez_dla_http("Silnik pod http://10.0.0.7:11434/v1 nie odpowiedział.")

    assert "10.0.0.7:11434" in wynik
    assert "/v1" not in wynik


def test_zawezenie_jest_idempotentne() -> None:
    """Dwukrotne zastosowanie nie może niczego dalej obcinać ani psuć."""
    tekst = "Silnik pod http://localhost:8000/v1; katalog /var/lib/husarz/audit."
    raz = zawez_dla_http(tekst)

    assert zawez_dla_http(raz) == raz
    assert raz != tekst, "założenie testu: pierwsze zastosowanie coś zmienia"
