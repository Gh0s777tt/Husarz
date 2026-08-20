"""Zestawy ewaluacyjne — model konfiguracji (Etap 16).

Moduł mieszka w warstwie ``husarz.config``, a nie w ``husarz.eval``, ŚWIADOMIE: to jest
model konfiguracji, wczytywany przez loader razem z resztą sekcji. Trzymanie go w pakiecie
wykonawczym tworzyło cykl importów (``config.schema`` -> ``eval`` -> ``agents`` ->
``config.schema``). Zależy wyłącznie od pydantica — warstwa niżej niż cokolwiek innego.

Zestaw to plik ``config/evals/<nazwa>.yaml`` opisujący przypadki, których wynik da się
sprawdzić **deterministycznie**, bez wołania modelu. To celowe ograniczenie pierwszego kroku:
weryfikator, który potrzebuje modelu, mierzy jakość MODELU; weryfikator deterministyczny
mierzy poprawność NASZEGO kodu — i tylko ten drugi może stać się bramką w CI.

Dwa rodzaje przypadków:

``routing``
    Czy agent trafi na oczekiwany model. Liczone czystą funkcją
    (``husarz.router.selection``) — bez sieci, bez backendu, w mikrosekundach. Wychwytuje
    dokładnie tę klasę błędu, którą naprawiał commit o panelu Agenci: rozjazd między tym,
    co config deklaruje, a tym, co router faktycznie wybierze.

``tool_policy``
    Czy bramka narzędziowa przepuści albo zablokuje wskazane narzędzie dla agenta.
    Uruchamia PRAWDZIWĄ pętlę narzędziową ze skryptowanym routerem, więc sprawdza realną
    ścieżkę autoryzacji (allowlista agenta, ROE, budżet), a nie samą deklarację w YAML-u.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """Baza z zakazem nieznanych pól — literówka w zestawie ma być błędem, nie ciszą."""

    model_config = ConfigDict(extra="forbid")


class RoutingCase(_StrictModel):
    """Oczekiwanie wobec wyboru modelu dla agenta.

    Attributes:
        name: krótki identyfikator przypadku (widoczny w raporcie).
        kind: dyskryminator rodzaju.
        agent: nazwa agenta z ``config/agents``.
        expect_model: identyfikator modelu, który MUSI zostać wybrany jako pierwszy kandydat.
        tags: opcjonalne tagi żądania (wpływają na reguły routingu).
    """

    name: str = Field(min_length=1)
    kind: Literal["routing"]
    agent: str = Field(min_length=1)
    expect_model: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class ToolPolicyCase(_StrictModel):
    """Oczekiwanie wobec bramki narzędziowej.

    Attributes:
        name: krótki identyfikator przypadku.
        kind: dyskryminator rodzaju.
        agent: nazwa agenta.
        tool: narzędzie, o które poprosi (skryptowany) model.
        action: akcja narzędzia.
        expect: ``allowed`` — wywołanie ma dojść do skutku; ``denied`` — bramka ma zablokować.
    """

    name: str = Field(min_length=1)
    kind: Literal["tool_policy"]
    agent: str = Field(min_length=1)
    tool: str = Field(min_length=1)
    action: str = Field(min_length=1)
    expect: Literal["allowed", "denied"]


# Dyskryminator MUSI stać przy unii, nie przy polu listy — inaczej pydantic odrzuca schemat.
EvalCase = Annotated[RoutingCase | ToolPolicyCase, Field(discriminator="kind")]


class EvalSet(_StrictModel):
    """Nazwany zestaw przypadków — jeden plik w ``config/evals/``.

    Attributes:
        name: nazwa zestawu (klucz w konfiguracji; musi zgadzać się z nazwą pliku).
        description: po co ten zestaw istnieje — czytane przez człowieka w raporcie.
        cases: przypadki do sprawdzenia.
    """

    name: str = Field(min_length=1)
    description: str = ""
    cases: list[EvalCase] = Field(default_factory=list)
