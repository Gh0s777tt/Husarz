"""Warstwa ewaluacji Husarza (Etap 16) — deterministyczny pomiar poprawności.

Zestawy w ``config/evals/*.yaml``; wykonanie: ``husarz eval``. Weryfikatory nie wołają modelu
ani sieci, więc nadają się na bramkę w CI. Modele zestawów: :mod:`husarz.config.evals`.
"""

from husarz.config.evals import EvalCase, EvalSet, RoutingCase, ToolPolicyCase
from husarz.eval.runner import CaseResult, SetResult, run_case, run_set

__all__ = [
    "CaseResult",
    "EvalCase",
    "EvalSet",
    "RoutingCase",
    "SetResult",
    "ToolPolicyCase",
    "run_case",
    "run_set",
]
