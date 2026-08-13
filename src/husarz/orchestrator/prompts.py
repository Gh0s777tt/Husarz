"""Instrukcje faz orkiestratora.

Znaczniki faz (``PHASE_*``) trafiają na początek wiadomości do modelu-hetmana,
by faza była jednoznaczna (a testy mogły deterministycznie odpowiadać). Instrukcje
wymuszają zwięzły, parsowalny format (JSON) planu i refleksji.
"""

from __future__ import annotations

PHASE_PLAN = "[FAZA:PLANOWANIE]"
PHASE_REFLECT = "[FAZA:REFLEKSJA]"
PHASE_SYNTH = "[FAZA:SYNTEZA]"

PLAN_INSTRUCTION = (
    "Zdekomponuj zadanie na kroki. Zwróć WYŁĄCZNIE JSON w formacie: "
    '{{"steps": [{{"agent": "<nazwa>", "task": "<opis>"}}]}}. '
    "Dozwoleni agenci: {agents}. Zadania w języku polskim kieruj do agenta 'bielik'."
)

REFLECT_INSTRUCTION = (
    "Oceń, czy obserwacje wystarczają do odpowiedzi. Zwróć WYŁĄCZNIE JSON: "
    '{"done": true/false, "additional_steps": [{"agent": "<nazwa>", "task": "<opis>"}]}.'
)

SYNTHESIS_INSTRUCTION = (
    "Na podstawie zadania i obserwacji zbuduj spójną, rzetelną odpowiedź końcową "
    "po polsku. Nie ujawniaj wewnętrznych znaczników faz."
)
