"""Strategie doboru modelu `cost` i `latency` (Etap 18h).

**Skąd ta zmiana.** Etap 17m odrzucił `routing.strategy: cost` i `latency` z konkretnej
przesłanki: `models.registry` nie przechowywał ani ceny, ani opóźnienia, więc strategia nie
miała czym routować i po cichu zachowywała się jak `tags`. Operator konfigurował politykę,
której nie było.

Kolejność naprawy była tu istotna i wynikała wprost z tamtej lekcji: **najpierw dane, potem
czytelnik**. Dodanie pól bez strategii, która je czyta, byłoby dokładnie tą samą wadą, tylko
przeniesioną o poziom niżej — polem, które wygląda na działające i nic nie robi.

Najważniejszy test w tym pliku to `test_cost_i_latency_daja_ROZNE_porzadki`. Bez niego cały
zestaw przechodziłby także wtedy, gdyby obie strategie po prostu sortowały tak samo — albo
wcale.
"""

from __future__ import annotations

import pytest

from husarz.config.errors import ConfigError
from husarz.config.loader import load_config
from husarz.config.schema import HusarzConfig, ModelSpec
from husarz.router.selection import select_candidates

TANI_WOLNY = {
    "backend": "mock",
    "model": "a",
    "tags": ["chat"],
    "cost_per_1m_input": 0.10,
    "cost_per_1m_output": 0.20,
    "latency_p50_ms": 5000,
}
DROGI_SZYBKI = {
    "backend": "mock",
    "model": "b",
    "tags": ["chat"],
    "cost_per_1m_input": 10.0,
    "cost_per_1m_output": 30.0,
    "latency_p50_ms": 120,
}


def _config(strategia: str, **routing: object) -> HusarzConfig:
    """Konfiguracja z dwoma modelami o przeciwstawnych profilach ceny i opóźnienia."""
    return HusarzConfig.model_validate(
        {
            "models": {
                "default": "tani_wolny",
                "registry": {"tani_wolny": TANI_WOLNY, "drogi_szybki": DROGI_SZYBKI},
            },
            "routing": {"strategy": strategia, **routing},
        }
    )


def test_cost_i_latency_daja_ROZNE_porzadki() -> None:
    """Sedno: obie strategie muszą realnie czytać SWOJE pole, nie jedno wspólne.

    Modele są dobrane przeciwstawnie (tani-wolny kontra drogi-szybki) właśnie po to, żeby
    porządki nie mogły przypadkiem się pokryć. Gdyby test używał modeli, w których tańszy
    jest zarazem szybszy, przechodziłby także wtedy, gdyby `latency` sortowało po koszcie.
    """
    po_koszcie = select_candidates(_config("cost"), tags=["chat"])
    po_opoznieniu = select_candidates(_config("latency"), tags=["chat"])

    assert po_koszcie == ["tani_wolny", "drogi_szybki"]
    assert po_opoznieniu == ["drogi_szybki", "tani_wolny"]
    assert po_koszcie != po_opoznieniu


def test_tags_zachowuje_kolejnosc_rejestru() -> None:
    """Zachowanie sprzed zmiany musi zostać nietknięte — to wartość domyślna."""
    assert select_candidates(_config("tags"), tags=["chat"]) == ["tani_wolny", "drogi_szybki"]


def test_strategia_NIE_nadpisuje_przypisania_agenta() -> None:
    """Granica strategii: jawna decyzja operatora jest ważniejsza od polityki.

    Gdyby `strategy: cost` przestawiało także `routing.agent_models`, przypisanie agenta do
    konkretnego modelu przestałoby cokolwiek znaczyć — a operator, który je wpisał, ma prawo
    oczekiwać, że obowiązuje. Strategia porządkuje WYŁĄCZNIE pulę dopasowaną tagami.
    """
    config = _config("cost", agent_models={"kopijnik": "drogi_szybki"})

    wynik = select_candidates(config, agent="kopijnik", tags=["chat"])

    assert wynik[0] == "drogi_szybki", "przypisanie agenta przegrało ze strategią"


def test_strategia_NIE_nadpisuje_reguly_prefer() -> None:
    """To samo dla `routing.rules[].prefer` — reguła jest uporządkowaną preferencją."""
    config = _config("cost", rules=[{"match_tags": ["chat"], "prefer": ["drogi_szybki"]}])

    assert select_candidates(config, tags=["chat"])[0] == "drogi_szybki"


def test_rowny_koszt_zachowuje_kolejnosc_rejestru() -> None:
    """Sortowanie jest STABILNE: przy remisie obowiązuje zachowanie strategii `tags`."""
    config = HusarzConfig.model_validate(
        {
            "models": {
                "default": "pierwszy",
                "registry": {
                    "pierwszy": {**TANI_WOLNY, "cost_per_1m_input": 1.0, "cost_per_1m_output": 1.0},
                    "drugi": {**DROGI_SZYBKI, "cost_per_1m_input": 1.0, "cost_per_1m_output": 1.0},
                },
            },
            "routing": {"strategy": "cost"},
        }
    )

    assert select_candidates(config, tags=["chat"]) == ["pierwszy", "drugi"]


def test_koszt_laczny_sumuje_obie_skladowe() -> None:
    """Klucz porządkowania jest sumą — model tańszy w obu składowych zawsze wypada wcześniej."""
    assert (
        ModelSpec(
            backend="mock", model="x", cost_per_1m_input=2.0, cost_per_1m_output=6.0
        ).koszt_laczny
        == 8.0
    )
    assert ModelSpec(backend="mock", model="x").koszt_laczny is None


def test_polowiczna_cena_jest_ODRZUCANA() -> None:
    """Model z jedną składową wyglądałby na tańszy od tych, które podały obie."""
    with pytest.raises(ValueError, match="OBU pól ceny"):
        ModelSpec(backend="mock", model="x", cost_per_1m_input=1.0)


# --------------------------------------------------------------------------------------
# Walidacja: strategia bez danych to polityka bez podstaw
# --------------------------------------------------------------------------------------

_MODELE_BEZ_DANYCH = (
    "default: a\nregistry:\n  a:\n    backend: mock\n    model: a\n    tags: [chat]\n"
)


@pytest.mark.parametrize(
    ("strategia", "fragment"),
    [("cost", "cost_per_1m_input"), ("latency", "latency_p50_ms")],
)
def test_strategia_bez_danych_jest_ODRZUCANA(write_config, strategia: str, fragment: str) -> None:
    """Bez danych model bez ceny wypadałby na końcu niezależnie od tego, czy jest drogi."""
    katalog = write_config(
        {"models.yaml": _MODELE_BEZ_DANYCH, "routing.yaml": f"strategy: {strategia}\n"}
    )

    with pytest.raises(ConfigError) as blad:
        load_config(katalog)

    tresc = str(blad.value)
    assert fragment in tresc, tresc
    assert "a" in tresc, "komunikat musi NAZWAĆ modele, którym brakuje danych"


def test_model_BEZ_tagow_nie_wymaga_danych(write_config) -> None:
    """Rygoryzm bez skutku byłby własną wadą.

    Model bez tagów nigdy nie trafia do puli porządkowanej strategią, więc żądanie od niego
    ceny zatrzymywałoby start bez żadnego zysku. Bez tej asercji test wyżej przechodziłby
    także wtedy, gdyby walidacja obejmowała CAŁY rejestr.
    """
    katalog = write_config(
        {
            "models.yaml": (
                "default: a\nregistry:\n"
                "  a:\n    backend: mock\n    model: a\n    tags: [chat]\n"
                "    cost_per_1m_input: 1.0\n    cost_per_1m_output: 2.0\n"
                "  bez_tagow:\n    backend: mock\n    model: b\n"
            ),
            "routing.yaml": "strategy: cost\n",
        }
    )

    config = load_config(katalog)

    assert config.routing.strategy.value == "cost"


def test_model_WYLACZONY_nie_wymaga_danych(write_config) -> None:
    """Model wyłączony i tak odpada przy rozwijaniu kandydatów."""
    katalog = write_config(
        {
            "models.yaml": (
                "default: a\nregistry:\n"
                "  a:\n    backend: mock\n    model: a\n    tags: [chat]\n"
                "    cost_per_1m_input: 1.0\n    cost_per_1m_output: 2.0\n"
                "  wylaczony:\n    backend: mock\n    model: b\n    tags: [chat]\n"
                "    enabled: false\n"
            ),
            "routing.yaml": "strategy: cost\n",
        }
    )

    assert load_config(katalog).routing.strategy.value == "cost"


def test_model_bez_danych_ladzie_na_KONCU_a_nie_na_poczatku() -> None:
    """„Nie wiem" nie może wyglądać jak „najtańszy" — także dla modelu WYŁĄCZONEGO.

    Wygląda to na przypadek bez znaczenia (walidacja nie dopuszcza braku danych wśród modeli
    włączonych i otagowanych, a wyłączone i tak odpadają), ale znaczenie ma i wykryła to
    dopiero kontrola nośności. Model wyłączony jest wprawdzie pomijany, lecz `_expand`
    ZSTĘPUJE po jego łańcuchu `fallback` — dokładnie tak, jak robi to router. Gdyby brak
    danych sortował się jako najtańszy, model zapasowy nieużywanego modelu wskakiwałby na
    POCZĄTEK listy kandydatów, wyprzedzając wybór dokonany strategią.
    """
    config = HusarzConfig.model_validate(
        {
            "models": {
                "default": "tani_wolny",
                "registry": {
                    "tani_wolny": TANI_WOLNY,
                    "drogi_szybki": DROGI_SZYBKI,
                    "wylaczony_bez_danych": {
                        "backend": "mock",
                        "model": "c",
                        "tags": ["chat"],
                        "enabled": False,
                        "fallback": ["drogi_szybki"],
                    },
                },
            },
            "routing": {"strategy": "cost"},
        }
    )

    assert select_candidates(config, tags=["chat"]) == ["tani_wolny", "drogi_szybki"]
