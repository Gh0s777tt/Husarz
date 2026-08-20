"""Materializacja i pomiar przebiegów agenta (Etap 16).

Rekord przebiegu niesie WYŁĄCZNIE metryki (rodzaj tury, narzędzie, wynik, długości, tokeny) —
nigdy treści promptów ani wyników narzędzi. Uzasadnienie: :mod:`husarz.runs.records`.
"""

from husarz.runs.records import RunRecord, RunStep, StepKind, Termination
from husarz.runs.store import JsonlRunStore, NullRunStore, RunStore, build_run_store

__all__ = [
    "JsonlRunStore",
    "NullRunStore",
    "RunRecord",
    "RunStep",
    "RunStore",
    "StepKind",
    "Termination",
    "build_run_store",
]
