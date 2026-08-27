"""Pola usunięte ze schematu — czytelna odmowa zamiast generycznego błędu.

`weights_path` żyło w `ModelSpec` i **nic go nie czytało**: jedyne wystąpienie w całym
repozytorium było w definicji pola. Wyglądało przy tym, jakby wskazywało silnikowi lokalne
wagi (nazwa, typ `Path`, komentarz „ścieżka do lokalnych wag"), więc operator mógł je ustawić
i uwierzyć, że coś z tego wynika. Martwe pole udające działające jest gorsze niż jego brak.

Usunięcie samo w sobie dałoby przy starcie „extra fields not permitted" — komunikat prawdziwy,
ale niczego nietłumaczący. Stąd osobny walidator z wyjaśnieniem.
"""

from __future__ import annotations

import pytest

from husarz.config.schema import ModelSpec

pytestmark = pytest.mark.unit


def test_weights_path_daje_KOMUNIKAT_a_nie_generyczny_blad() -> None:
    """Operator ma się dowiedzieć, CO się stało i dlaczego pole zniknęło."""
    with pytest.raises(ValueError) as exc:
        ModelSpec(backend="ollama", model="x", weights_path="./models/x")

    tresc = str(exc.value)
    assert "weights_path" in tresc
    assert "USUNIĘTE" in tresc
    assert "nie było przez nic czytane" in tresc
    # Bez tego asercje wyżej przechodziłyby także dla generycznego błędu Pydantica,
    # gdyby ten przypadkiem cytował nazwę pola.
    assert "extra_forbidden" not in tresc


def test_poprawna_specyfikacja_bez_tego_pola_przechodzi() -> None:
    """Nośność: walidator nie może odrzucać wszystkiego."""
    spec = ModelSpec(backend="ollama", model="x", endpoint="http://localhost:11434/v1")

    assert spec.model == "x"
    assert not hasattr(spec, "weights_path"), "pole miało zniknąć, nie zostać ukryte"


def test_dostarczona_konfiguracja_nie_uzywa_usunietego_pola(
    repo_config_dir,
) -> None:  # noqa: ANN001
    """Regresja: usuwając pole ze schematu, ZOSTAWIŁEM je w trzech miejscach configu repo.

    Wyłapał to dopiero `husarz validate` — grep po `src/` go nie widział, bo szukałem
    w kodzie, a nie w konfiguracji. Ten test pilnuje, żeby nie wróciło.
    """
    from husarz.config import load_config

    load_config(repo_config_dir)  # rzuciłoby, gdyby pole gdzieś zostało
    tresc = (repo_config_dir / "models.yaml").read_text(encoding="utf-8")
    assert "weights_path" not in tresc
