"""Testy launchera CLI (podkomendy validate/version)."""

from __future__ import annotations

from pathlib import Path

import pytest

from husarz.launcher.cli import main

pytestmark = pytest.mark.unit


def test_validate_ok_on_repo_config(repo_config_dir: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(["validate", "--config", str(repo_config_dir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "wczytana poprawnie" in out
    assert "puszkarz" in out


def test_validate_fails_on_broken_config(write_config, capsys: pytest.CaptureFixture) -> None:
    config_dir = write_config(
        {"models.yaml": "default: ghost\nregistry:\n  m1:\n    backend: mock\n    model: x\n"}
    )
    rc = main(["validate", "--config", str(config_dir)])
    err = capsys.readouterr().err
    assert rc == 1
    assert "niepoprawna" in err.lower() or "ghost" in err


def test_version(capsys: pytest.CaptureFixture) -> None:
    rc = main(["version"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Husarz" in out


def test_up_passes_resolved_config_dir_to_app(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """REGRESJA z uruchomienia realnej aplikacji: `husarz up` bez jawnego --config startował
    z `config_dir=None`, więc `POST /api/config/runtime` odpowiadał „Nadpisania wymagają
    katalogu konfiguracji" — panel konfiguracji w konsoli był martwy, mimo że konfiguracja
    wczytała się z tego samego (domyślnego) katalogu."""
    import argparse

    import uvicorn

    import husarz.api
    from husarz.launcher import cli

    captured: dict[str, object] = {}

    def fake_create_app(config, **kwargs):  # noqa: ANN001, ANN202
        captured.update(kwargs)
        return object()

    # `create_app` i `uvicorn` są importowane LENIWIE wewnątrz `_cmd_up` — podmieniamy je
    # w modułach źródłowych. Bez podmiany `uvicorn.run` test zawisłby na serwerze.
    monkeypatch.setattr(husarz.api, "create_app", fake_create_app)
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "models.yaml").write_text(
        "default: m1\nregistry:\n  m1:\n    backend: mock\n    model: x\n", encoding="utf-8"
    )
    args = argparse.Namespace(
        config=None,
        profile=None,
        host="127.0.0.1",
        port=8000,
        prompts="./prompts",
        allow_insecure=False,
    )
    assert cli._cmd_up(args) == 0
    assert captured.get("config_dir") is not None, "config_dir MUSI być rozwiązany, nie None"


# --- `husarz doctor --probe`: sonda głęboka jako świadomy opt-in --------------


def _przechwyc_zdiagnozuj(monkeypatch) -> dict:  # noqa: ANN001
    """Podmienia `zdiagnozuj` i zwraca słownik z argumentami, które do niej trafiły."""
    import husarz.launcher.doctor as modul_diagnozy

    zapis: dict = {}

    def _fake(config, **kwargs):  # noqa: ANN001, ANN202
        zapis.update(kwargs)
        return []

    monkeypatch.setattr(modul_diagnozy, "zdiagnozuj", _fake)
    return zapis


def test_doctor_bez_probe_NIE_dostaje_sondy_glebokiej(
    repo_config_dir: Path, monkeypatch
) -> None:  # noqa: ANN001
    """Opt-in jest strukturalny: bez flagi diagnoza nie ma CZYM zapytać modelu.

    Zapytanie modelu ma skutki uboczne (wczytuje wagi, potrafi trwać minuty), więc nie może
    włączyć się przez przeoczenie.
    """
    zapis = _przechwyc_zdiagnozuj(monkeypatch)

    rc = main(["doctor", "--config", str(repo_config_dir)])

    assert rc == 0
    assert zapis["sonda_gleboka"] is None


def test_doctor_z_probe_dostaje_sonde_gleboka(
    repo_config_dir: Path, monkeypatch
) -> None:  # noqa: ANN001
    """Nośność powyższego: z flagą sonda MUSI dotrzeć, inaczej test niczego nie chroni."""
    zapis = _przechwyc_zdiagnozuj(monkeypatch)

    rc = main(["doctor", "--config", str(repo_config_dir), "--probe"])

    assert rc == 0
    assert zapis["sonda_gleboka"] is not None
    assert hasattr(zapis["sonda_gleboka"], "zapytaj_model")


def test_probe_timeout_dociera_do_sondy(repo_config_dir: Path, monkeypatch) -> None:  # noqa: ANN001
    """`--probe-timeout` musi realnie zmieniać limit, a nie tylko istnieć w pomocy.

    Bez tego operator, którego model wczytuje się dwie minuty, podnosiłby limit bez skutku
    i dostawał w kółko „timeout" — czyli diagnozę mówiącą nieprawdę o przyczynie.
    """
    zapis = _przechwyc_zdiagnozuj(monkeypatch)

    main(["doctor", "--config", str(repo_config_dir), "--probe", "--probe-timeout", "7"])

    sonda = zapis["sonda_gleboka"]
    assert sonda._timeout_zapytania == 7


def test_probe_timeout_odrzuca_wartosc_bezuzyteczna(
    repo_config_dir: Path, capsys
) -> None:  # noqa: ANN001
    """`--probe-timeout 0` przerywałby KAŻDE żądanie natychmiast i diagnozował sprawny
    silnik jako awarię.

    `model_copy(update=...)` OMIJA walidację schematu (`ge=1`), więc wartość docierała do
    klienta bez żadnej kontroli. Narzędzie pomiarowe musi odmówić pomiaru, którego nie da się
    wykonać, zamiast zmyślać wynik.
    """
    for zla in ["0", "-5"]:
        with pytest.raises(SystemExit) as exc:
            main(["doctor", "--config", str(repo_config_dir), "--probe", "--probe-timeout", zla])
        assert exc.value.code == 2, zla
    assert "co najmniej 1 s" in capsys.readouterr().err


def test_probe_respektuje_kill_switch_magazynu_sekretow(
    repo_config_dir: Path, monkeypatch
) -> None:  # noqa: ANN001
    """Wyłączony `security.secret_store` to kill-switch, także dla diagnozy.

    Operator wyłączający magazyn — zwykle w reakcji na incydent — oczekuje, że przestanie on
    wydawać materiał. Domyślne `magazyn_dostepny=True` w dostawcy sekretów obchodziłoby to.
    """
    zapis = _przechwyc_zdiagnozuj(monkeypatch)

    main(["doctor", "--config", str(repo_config_dir), "--probe"])

    from husarz.config import load_config

    wlaczony = load_config(repo_config_dir).security.secret_store.enabled
    # Warunek wstępny nazwany WPROST: gdyby konfiguracja repo miała magazyn włączony,
    # asercja niżej przechodziłaby także dla domyślnego `_SchemeSecrets()` — czyli test
    # przestałby cokolwiek sprawdzać, nie dając o tym znaku. Tu zamiast cichej degradacji
    # dostajemy głośną porażkę z instrukcją.
    assert wlaczony is False, (
        "ten test wymaga konfiguracji z WYŁĄCZONYM magazynem sekretów — inaczej nie odróżnia "
        "poprawnego przekazania od wartości domyślnej. Zbuduj własny katalog konfiguracji."
    )
    dostawca = zapis["sonda_gleboka"]._secrets
    assert (
        dostawca._magazyn_dostepny is wlaczony
    ), "dostawca sekretów sondy nie odzwierciedla ustawienia z konfiguracji"


def test_up_przekazuje_sekrety_do_routera(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    """REGRESJA (wada sprzed sondy, ujawniona przez nią): router w `husarz up` dostawał
    `NullSecretsProvider`.

    Skutek: KAŻDY model z `api_key_ref` był w produkcji nieużywalny — `build_client` zgłaszał
    „Nie udało się rozwiązać sekretu klucza API" i żądanie nie wychodziło. Dostarczona
    konfiguracja nie używa `api_key_ref`, więc nic tego nie wywoływało; sonda głęboka
    rozwiązywała klucz i meldowała OK dla drogi, której router nie potrafił przejść.
    """
    import husarz.launcher.cli as cli

    przechwycone: dict = {}

    def _fake_create_app(config, **kwargs):  # noqa: ANN001, ANN202
        przechwycone["router"] = kwargs["router_factory"](config)
        raise SystemExit(0)

    monkeypatch.setattr("husarz.api.create_app", _fake_create_app)
    monkeypatch.setattr(cli, "_open_browser_async", lambda url: None)

    with pytest.raises(SystemExit):
        main(["up", "--host", "127.0.0.1", "--port", "8123"])

    dostawca = przechwycone["router"]._secrets
    assert (
        type(dostawca).__name__ != "NullSecretsProvider"
    ), "router produkcyjny nie potrafi rozwiązać `api_key_ref` żadnego modelu"
    assert dostawca.resolve("env:NIE_MA_TAKIEJ_ZMIENNEJ") is None
