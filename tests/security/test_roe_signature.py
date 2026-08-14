"""Niezmienniki kryptograficznego podpisu ROE (Etap 4b, ADR-0021).

ROE to jedyny artefakt uprawniający Puszkarza do AKTYWNYCH działań wobec konkretnych celów.
Do tej pory bramka sprawdzała wyłącznie, czy pole ``signature`` jest niepuste — czyli
dopisanie ``signature: "abc"`` czyniło zlecenie ważnym. Testy poniżej pilnują, że:

1. podpis obejmuje CAŁĄ treść autoryzacyjną (zmiana dowolnego pola go unieważnia),
2. w szczególności poszerzenie zakresu i podniesienie ``consent`` — bo to są realne
   ścieżki eskalacji (Husarz scala config z wielu warstw, także nadpisań runtime),
3. każdy błąd (zły klucz, zły format, inny algorytm, brak klucza) kończy się ODMOWĄ,
4. bramka ``RoeGate`` faktycznie honoruje weryfikator.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest

from husarz.config.schema import RoeConfig, RoeSignatureConfig, SecurityConfig
from husarz.security import AuditLog, RoeGate, build_roe_verifier
from husarz.security.roe_signature import (
    RoeSignatureError,
    canonical_payload,
    sign_ed25519,
    sign_hmac,
    verify,
)

pytestmark = pytest.mark.security

_KEY = "wspoldzielony-sekret-zlecenia"
NOW = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


def _roe(**overrides: Any) -> RoeConfig:
    base: dict[str, Any] = {
        "engagement_id": "zlecenie-1",
        "owner": "Klient sp. z o.o.",
        "authorized_by": "CISO klienta",
        "scope": {
            "targets_cidr": ["192.0.2.0/24"],
            "targets_domains": ["app.example.local"],
            "out_of_scope": ["192.0.2.1"],
        },
        "window": {
            "start": datetime(2026, 9, 1, 8, 0, tzinfo=UTC),
            "end": datetime(2026, 9, 1, 18, 0, tzinfo=UTC),
        },
        "allowed_techniques": ["port-scan"],
        "forbidden_techniques": ["denial-of-service"],
        "consent": True,
    }
    base.update(overrides)
    return RoeConfig(**base)


def _signed(**overrides: Any) -> RoeConfig:
    roe = _roe(**overrides)
    return roe.model_copy(update={"signature": sign_hmac(roe, _KEY)})


class DictSecrets:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def resolve(self, ref: str) -> str | None:
        return self._values.get(ref)


# --- Kanoniczny payload -----------------------------------------------------


def test_payload_is_deterministic_and_domain_separated() -> None:
    roe = _roe()
    assert canonical_payload(roe) == canonical_payload(_roe())
    assert canonical_payload(roe).startswith(b"husarz-roe-v1\n")


def test_payload_ignores_signature_field() -> None:
    """Podpis nie może wchodzić do własnego payloadu — inaczej nie dałoby się go policzyć."""
    roe = _roe()
    with_sig = roe.model_copy(update={"signature": "hmac-sha256:AAAA"})
    assert canonical_payload(roe) == canonical_payload(with_sig)


# --- Wykrywanie manipulacji -------------------------------------------------


def test_valid_signature_verifies() -> None:
    assert verify(_signed(), algorithm="hmac-sha256", key=_KEY) is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("engagement_id", "inne-zlecenie"),
        ("owner", "Ktoś inny"),
        ("authorized_by", "Nie ta osoba"),
        ("allowed_techniques", ["port-scan", "exploit"]),
        ("forbidden_techniques", []),
        ("dry_run_default", False),
    ],
)
def test_any_field_change_invalidates_signature(field: str, value: Any) -> None:
    tampered = _signed().model_copy(update={field: value})
    assert verify(tampered, algorithm="hmac-sha256", key=_KEY) is False


def test_scope_widening_invalidates_signature() -> None:
    """Najważniejszy scenariusz: dopisanie celu spoza zlecenia = atak na osobę trzecią."""
    signed = _signed()
    widened = signed.model_copy(
        update={"scope": signed.scope.model_copy(update={"targets_cidr": ["0.0.0.0/0"]})}
    )
    assert verify(widened, algorithm="hmac-sha256", key=_KEY) is False


def test_removing_out_of_scope_exclusion_invalidates_signature() -> None:
    """Usunięcie wyłączenia (host krytyczny) też jest poszerzeniem uprawnień."""
    signed = _signed()
    stripped = signed.model_copy(
        update={"scope": signed.scope.model_copy(update={"out_of_scope": []})}
    )
    assert verify(stripped, algorithm="hmac-sha256", key=_KEY) is False


def test_window_extension_invalidates_signature() -> None:
    signed = _signed()
    longer = signed.model_copy(
        update={
            "window": signed.window.model_copy(
                update={"end": datetime(2027, 1, 1, 0, 0, tzinfo=UTC)}
            )
        }
    )
    assert verify(longer, algorithm="hmac-sha256", key=_KEY) is False


def test_raising_consent_invalidates_signature() -> None:
    """Podpisany szablon BEZ zgody nie może stać się ważnym zleceniem przez flip flagi."""
    signed = _signed(consent=False)
    assert verify(signed, algorithm="hmac-sha256", key=_KEY) is True
    escalated = signed.model_copy(update={"consent": True})
    assert verify(escalated, algorithm="hmac-sha256", key=_KEY) is False


def test_wrong_key_does_not_verify() -> None:
    assert verify(_signed(), algorithm="hmac-sha256", key="inny-sekret") is False


# --- Odporność formatu (każdy błąd = odmowa, nie wyjątek) -------------------


@pytest.mark.parametrize(
    "signature",
    [
        "abc",  # dawny „podpis": dowolny tekst — teraz NIEWAŻNY
        "",
        "   ",
        "hmac-sha256:",  # pusty materiał
        "hmac-sha256:!!!nie-base64!!!",
        "nieznany-alg:AAAA",
        "AAAA",  # brak separatora
    ],
)
def test_malformed_signature_is_denied_not_crash(signature: str) -> None:
    roe = _roe().model_copy(update={"signature": signature})
    assert verify(roe, algorithm="hmac-sha256", key=_KEY) is False


def test_missing_signature_is_denied() -> None:
    assert verify(_roe(signature=None), algorithm="hmac-sha256", key=_KEY) is False


def test_algorithm_downgrade_is_denied() -> None:
    """Plik deklarujący inny algorytm niż skonfigurowany nie może „zgadzać się sam ze sobą"."""
    assert verify(_signed(), algorithm="ed25519", key=_ed25519_keys()[1]) is False


def test_empty_key_is_configuration_error_not_silent_pass() -> None:
    with pytest.raises(RoeSignatureError):
        verify(_signed(), algorithm="hmac-sha256", key="   ")


def test_unknown_configured_algorithm_is_error() -> None:
    with pytest.raises(RoeSignatureError):
        verify(_signed(), algorithm="rot13", key=_KEY)


# --- Ed25519 (klucz prywatny poza maszyną) ----------------------------------


def _ed25519_keys() -> tuple[str, str]:
    """Zwraca ``(klucz_prywatny_PEM, klucz_publiczny_PEM)``."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("utf-8")
    public = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8")
    )
    return pem, public


def test_ed25519_round_trip_pem_and_raw_base64() -> None:
    private_pem, public_pem = _ed25519_keys()
    roe = _roe()
    signed = roe.model_copy(update={"signature": sign_ed25519(roe, private_pem)})
    assert verify(signed, algorithm="ed25519", key=public_pem) is True

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    raw = load_pem_public_key(public_pem.encode()).public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    assert verify(signed, algorithm="ed25519", key=base64.b64encode(raw).decode()) is True


def test_ed25519_tampered_scope_is_denied() -> None:
    private_pem, public_pem = _ed25519_keys()
    roe = _roe()
    signed = roe.model_copy(update={"signature": sign_ed25519(roe, private_pem)})
    widened = signed.model_copy(
        update={"scope": signed.scope.model_copy(update={"targets_cidr": ["10.0.0.0/8"]})}
    )
    assert verify(widened, algorithm="ed25519", key=public_pem) is False


def test_ed25519_other_key_does_not_verify() -> None:
    private_pem, _ = _ed25519_keys()
    _, other_public = _ed25519_keys()
    roe = _roe()
    signed = roe.model_copy(update={"signature": sign_ed25519(roe, private_pem)})
    assert verify(signed, algorithm="ed25519", key=other_public) is False


def test_ed25519_bad_public_key_is_configuration_error() -> None:
    private_pem, _ = _ed25519_keys()
    roe = _roe()
    signed = roe.model_copy(update={"signature": sign_ed25519(roe, private_pem)})
    with pytest.raises(RoeSignatureError):
        verify(signed, algorithm="ed25519", key="to-nie-jest-klucz")


# --- Fabryka weryfikatora + wpięcie w bramkę --------------------------------


def _security(**overrides: Any) -> SecurityConfig:
    return SecurityConfig(roe=RoeSignatureConfig(**overrides))


def test_verifier_disabled_returns_none() -> None:
    assert build_roe_verifier(_security(verify_signature=False)) is None


def test_verifier_enabled_without_key_ref_fails_closed_at_startup() -> None:
    with pytest.raises(RoeSignatureError):
        build_roe_verifier(_security(verify_signature=True, key_ref=None))


def test_verifier_enabled_without_secrets_provider_fails_closed() -> None:
    with pytest.raises(RoeSignatureError):
        build_roe_verifier(_security(key_ref="env:ROE"), None)


def test_verifier_unresolvable_key_raises_never_passes() -> None:
    verifier = build_roe_verifier(
        _security(algorithm="hmac-sha256", key_ref="env:ROE"), DictSecrets({})
    )
    assert verifier is not None
    with pytest.raises(RoeSignatureError):
        verifier(_signed())


def test_verifier_resolves_key_lazily_on_each_call() -> None:
    """Klucz nie jest zapamiętywany — rotacja sekretu obowiązuje bez restartu."""

    class CountingSecrets:
        def __init__(self) -> None:
            self.calls = 0

        def resolve(self, ref: str) -> str | None:
            self.calls += 1
            return _KEY

    secrets = CountingSecrets()
    verifier = build_roe_verifier(_security(algorithm="hmac-sha256", key_ref="env:ROE"), secrets)
    assert verifier is not None
    verifier(_signed())
    verifier(_signed())
    assert secrets.calls == 2


def test_gate_denies_when_signature_invalid() -> None:
    """Bramka honoruje weryfikator: ROE z podrobionym podpisem nie autoryzuje niczego."""
    audit = AuditLog()
    verifier = build_roe_verifier(
        _security(algorithm="hmac-sha256", key_ref="env:ROE"), DictSecrets({"env:ROE": _KEY})
    )
    forged = _roe().model_copy(update={"signature": "hmac-sha256:AAAAAAAAAAAAAAAAAAAAAA=="})
    decision = RoeGate(forged, audit, signature_verifier=verifier).evaluate(
        target="192.0.2.10", technique="port-scan", now=NOW
    )
    assert decision.allowed is False
    assert "Podpis" in decision.reason
    assert any(entry.action == "roe.deny" for entry in audit.entries)


def test_gate_allows_with_valid_signature() -> None:
    audit = AuditLog()
    verifier = build_roe_verifier(
        _security(algorithm="hmac-sha256", key_ref="env:ROE"), DictSecrets({"env:ROE": _KEY})
    )
    decision = RoeGate(_signed(), audit, signature_verifier=verifier).evaluate(
        target="192.0.2.10", technique="port-scan", now=NOW
    )
    assert decision.allowed is True


# --- Walidacja krzyżowa configu i narzędzie operatora (CLI) -----------------


def test_prod_with_consented_roe_requires_signature_verification(
    write_config,
) -> None:  # noqa: ANN001
    """Profil prod/airgap z AKTYWNYM zleceniem nie może mieć wyłączonej weryfikacji."""
    from husarz.config import load_config
    from husarz.config.errors import ConfigError

    files = {
        "models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n",
        "husarz.yaml": "profile: prod\n",
        "security.yaml": (
            "sandbox:\n  engine: docker\n  image: husarz-sandbox\n"
            "audit:\n  enabled: true\n  immutable: true\n"
            "encryption:\n  at_rest: true\n"
            "roe:\n  verify_signature: false\n"
        ),
        "roe/aktywne.yaml": (
            "engagement_id: aktywne\nowner: o\nauthorized_by: a\n"
            "scope:\n  targets_cidr: [192.0.2.0/24]\n"
            "window:\n  start: 2026-09-01T08:00:00Z\n  end: 2026-09-01T18:00:00Z\n"
            "consent: true\nsignature: cokolwiek\n"
        ),
    }
    with pytest.raises(ConfigError, match="weryfikacji podpisu"):
        load_config(write_config(files))


def test_prod_without_consented_roe_does_not_require_key(write_config) -> None:  # noqa: ANN001
    """Sam SZABLON bez zgody nie wymusza klucza — nie ma po co obciążać wdrożeń bez zleceń."""
    from husarz.config import load_config

    files = {
        "models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n",
        "husarz.yaml": "profile: prod\n",
        "security.yaml": (
            "sandbox:\n  engine: docker\n  image: husarz-sandbox\n"
            "audit:\n  enabled: true\n  immutable: true\n"
            "encryption:\n  at_rest: true\n"
            "roe:\n  verify_signature: true\n"
        ),
        "roe/szablon.yaml": (
            "engagement_id: szablon\nowner: o\nauthorized_by: a\n"
            "scope:\n  targets_cidr: [192.0.2.0/24]\n"
            "window:\n  start: 2026-09-01T08:00:00Z\n  end: 2026-09-01T18:00:00Z\n"
            "consent: false\n"
        ),
    }
    assert load_config(write_config(files)).platform.profile.value == "prod"


def test_cli_roe_sign_then_verify_round_trip(
    write_config,  # noqa: ANN001
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Any,
) -> None:
    """Narzędzie operatora domyka pętlę: bez `roe sign` funkcji nie dałoby się użyć."""
    from husarz.launcher.cli import main

    roe_yaml = (
        "engagement_id: zlecenie\nowner: o\nauthorized_by: a\n"
        "scope:\n  targets_cidr: [192.0.2.0/24]\n"
        "window:\n  start: 2026-09-01T08:00:00Z\n  end: 2026-09-01T18:00:00Z\n"
        "consent: true\n"
    )
    files = {
        "models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n",
        "security.yaml": "roe:\n  algorithm: hmac-sha256\n  key_ref: env:HUSARZ_ROE_KEY\n",
        "roe/zlecenie.yaml": roe_yaml,
    }
    config_dir = write_config(files)
    monkeypatch.setenv("HUSARZ_ROE_KEY", _KEY)

    assert main(["roe", "sign", "--config", str(config_dir), "--engagement", "zlecenie"]) == 0
    printed = capsys.readouterr().out
    signature = printed.split("signature:", 1)[1].strip()
    assert signature.startswith("hmac-sha256:")

    # Bez podpisu weryfikacja odmawia (kod 2)...
    assert main(["roe", "verify", "--config", str(config_dir), "--engagement", "zlecenie"]) == 2
    # ...a po wklejeniu wartości do pliku — przechodzi (kod 0).
    (config_dir / "roe" / "zlecenie.yaml").write_text(
        roe_yaml + f"signature: {signature}\n", encoding="utf-8"
    )
    assert main(["roe", "verify", "--config", str(config_dir), "--engagement", "zlecenie"]) == 0


def test_cli_roe_sign_unknown_engagement_fails(write_config) -> None:  # noqa: ANN001
    from husarz.launcher.cli import main

    files = {"models.yaml": "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n"}
    assert (
        main(["roe", "sign", "--config", str(write_config(files)), "--engagement", "nie-ma"]) == 1
    )
