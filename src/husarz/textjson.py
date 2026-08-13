"""Odporne wyłuskiwanie obiektu JSON z tekstu modelu (wspólny helper).

Używane przez planowanie orkiestratora (``orchestrator/plan.py``) i protokół ReAct
pętli narzędziowej (``agents/react.py``). Kontrakt: szum/proza/wiele obiektów →
zwrot pierwszego poprawnego obiektu albo ``None`` — NIGDY wyjątek.
"""

from __future__ import annotations

import json
from typing import Any

_JSON_DECODER = json.JSONDecoder()


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Wyłuskuje PIERWSZY poprawny obiekt JSON z tekstu (czysty lub osadzony w prozie).

    Skanuje kolejne pozycje ``{`` i próbuje ``raw_decode`` — dzięki temu obce nawiasy
    w prozie czy wiele obiektów nie psują wyniku (inaczej niż ``find``/``rfind``).
    Nigdy nie rzuca: ``RecursionError``/``JSONDecodeError`` na złośliwym wejściu kończy
    się ``None`` (szum modelu → brak obiektu, nie wyjątek).
    """
    stripped = text.strip()
    try:
        whole = json.loads(stripped)
        if isinstance(whole, dict):
            return whole
    except (json.JSONDecodeError, RecursionError):
        pass

    index = stripped.find("{")
    while index != -1:
        try:
            candidate, _ = _JSON_DECODER.raw_decode(stripped, index)
        except (json.JSONDecodeError, RecursionError):
            candidate = None
        if isinstance(candidate, dict):
            return candidate
        index = stripped.find("{", index + 1)
    return None
