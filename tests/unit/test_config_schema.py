"""Testy walidacji na poziomie schematu (Pydantic) — czytelne błędy zamiast crasha."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from husarz.config.schema import (
    ModelsConfig,
    ModelSpec,
    PlatformConfig,
    RoeConfig,
    RoeScope,
    RoeWindow,
    ToolConfig,
)

pytestmark = pytest.mark.unit


def test_telemetry_is_forbidden() -> None:
    """Zero telemetrii — włączenie telemetrii jest błędem."""
    with pytest.raises(ValidationError, match="[Tt]elemetria"):
        PlatformConfig(telemetry_enabled=True)


def test_unknown_field_is_rejected() -> None:
    """Nieznane pole (literówka) = błąd, nie ciche zignorowanie."""
    with pytest.raises(ValidationError):
        PlatformConfig(profil="dev")  # celowa literówka: 'profil' zamiast 'profile'


def test_models_default_must_exist() -> None:
    """models.default musi wskazywać model z rejestru."""
    with pytest.raises(ValidationError, match="default"):
        ModelsConfig(default="brak", registry={"m1": ModelSpec(backend="mock", model="x")})


def test_model_fallback_must_exist() -> None:
    """Fallback musi istnieć w rejestrze."""
    with pytest.raises(ValidationError, match="fallback"):
        ModelsConfig(
            default="m1",
            registry={"m1": ModelSpec(backend="mock", model="x", fallback=["ghost"])},
        )


def test_model_self_fallback_rejected() -> None:
    """Model nie może być własnym fallbackiem."""
    with pytest.raises(ValidationError, match="własnym fallbackiem"):
        ModelsConfig(
            default="m1",
            registry={"m1": ModelSpec(backend="mock", model="x", fallback=["m1"])},
        )


def test_tool_egress_requires_allowlist() -> None:
    """Narzędzie z egress bez allowlisty = błąd."""
    with pytest.raises(ValidationError, match="allowlist"):
        ToolConfig(name="web", kind="web", requires_egress=True, allowlist=[])


def test_roe_scope_requires_targets() -> None:
    """ROE bez celów = błąd."""
    with pytest.raises(ValidationError, match="cel"):
        RoeScope()


def test_roe_window_order() -> None:
    """ROE: koniec okna musi być po początku."""
    with pytest.raises(ValidationError, match="end"):
        RoeWindow(start=datetime(2026, 9, 1, 17), end=datetime(2026, 9, 1, 9))


def test_roe_inactive_without_consent_and_signature() -> None:
    """ROE bez zgody i podpisu jest nieaktywne (is_active == False)."""
    roe = RoeConfig(
        engagement_id="e1",
        owner="o",
        authorized_by="a",
        scope=RoeScope(targets_domains=["app.example.local"]),
        window=RoeWindow(start=datetime(2026, 9, 1, 9), end=datetime(2026, 9, 1, 17)),
        consent=False,
    )
    assert roe.is_active is False

    roe_signed = roe.model_copy(update={"consent": True, "signature": "sops:ref"})
    assert roe_signed.is_active is True
