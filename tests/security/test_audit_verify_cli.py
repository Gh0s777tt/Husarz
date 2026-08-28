"""`husarz audit verify` — weryfikacja dziennika z wiersza poleceń.

**Skąd to polecenie.** Przy diagnozowaniu rozgałęzionego dziennika projektu (Etap 18c)
trzeba było napisać jednorazowy skrypt, żeby dowiedzieć się, że pęknięcie jest na wpisie
nr 261. Odpowiedź na pytanie „GDZIE" była wtedy jedyną, która się liczyła — sam werdykt
„coś jest nie tak" nie prowadził donikąd. Skoro potrzebna raz, będzie potrzebna znowu.

Polecenie różni się od startu platformy w rzeczy zasadniczej: `build_audit_log` ODMAWIA
działania na uszkodzonym dzienniku (bo buduje dziennik do PISANIA), a narzędzie
diagnostyczne ma uszkodzenie POKAZAĆ. Dziennik, którego nie da się obejrzeć dokładnie
wtedy, gdy coś jest z nim nie tak, byłby bezużyteczny w jedynym momencie, który się liczy.
Pilnuje tego `test_raport_dziala_na_dzienniku_ktory_BLOKUJE_start`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from husarz.launcher.cli import main
from husarz.security.audit import AuditLog

pytestmark = pytest.mark.security

_MODELE = "default: m\nregistry:\n  m:\n    backend: mock\n    model: testowy\n"


@pytest.fixture(autouse=True)
def _bez_nadpisania_sciezki(monkeypatch: pytest.MonkeyPatch) -> None:
    """Zdejmuje globalne nadpisanie `audit.path` z warstwy ENV.

    Fikstura z `conftest.py` kieruje dziennik do `tmp_path` w KAŻDYM teście, żeby przebieg
    nie pisał do prawdziwego dziennika operatora. Tutaj przeszkadza: te testy sprawdzają,
    czy polecenie czyta dziennik wskazany w PLIKU konfiguracji, a warstwa ENV wygrywa
    z plikami — więc bez tego zdjęcia badałyby pusty dziennik i przechodziły z niewłaściwego
    powodu (sprawdzone: raport meldował „ŁAŃCUCH SPÓJNY" mimo podmienionego wpisu).

    Dzienniki tych testów i tak leżą w `tmp_path`, więc nic nie trafia do repozytorium.
    """
    monkeypatch.delenv("HUSARZ_SECURITY__AUDIT__PATH", raising=False)


def _instalacja(write_config, tmp_path: Path, wpisow: int = 4) -> tuple[Path, Path]:
    """Buduje katalog konfiguracji ze wskazanym dziennikiem i zwraca (config, dziennik)."""
    dziennik = tmp_path / "dane" / "audit.log"
    katalog = write_config(
        {
            "models.yaml": _MODELE,
            "security.yaml": f"audit:\n  path: {dziennik}\n",
        }
    )
    log = AuditLog(path=dziennik, anchor_path=dziennik.with_name("audit.log.kotwica"))
    for i in range(wpisow):
        log.record("api", f"akcja-{i}", {"sciezka": "/tajna/sciezka/operatora"})
    return katalog, dziennik


def test_spojny_dziennik_daje_kod_0(write_config, tmp_path: Path, capsys) -> None:
    """Ścieżka pogodna — i zarazem sprawdzenie, że raport nazywa stan kotwicy."""
    katalog, _ = _instalacja(write_config, tmp_path)

    kod = main(["audit", "verify", "--config", str(katalog)])

    wyjscie = capsys.readouterr().out
    assert kod == 0
    assert "ŁAŃCUCH SPÓJNY" in wyjscie
    assert "Kotwica:   ok" in wyjscie


def test_rozgalezienie_wskazuje_KONKRETNY_wpis(write_config, tmp_path: Path, capsys) -> None:
    """Odtwarza układ z realnego dziennika: wpis wskazujący na starszą głowę łańcucha."""
    katalog, dziennik = _instalacja(write_config, tmp_path)
    linie = dziennik.read_text(encoding="utf-8").strip().splitlines()
    wpis = json.loads(linie[3])
    wpis["prev_hash"] = json.loads(linie[0])["entry_hash"]  # cofnięty o dwa ogniwa
    linie[3] = json.dumps(wpis, ensure_ascii=False)
    dziennik.write_text("\n".join(linie) + "\n", encoding="utf-8")

    kod = main(["audit", "verify", "--config", str(katalog)])

    zebrane = capsys.readouterr()
    assert kod == 1
    assert "NIEZGODNOŚĆ (ogniwo)" in zebrane.err
    assert "wpis nr 3" in zebrane.err, zebrane.err
    assert "akcja-3" in zebrane.err


def test_odciecie_ogona_jest_nazwane_kotwica(write_config, tmp_path: Path, capsys) -> None:
    """Rodzaj niezgodności ma prowadzić do przyczyny, a nie tylko oznajmiać porażkę."""
    katalog, dziennik = _instalacja(write_config, tmp_path)
    linie = dziennik.read_text(encoding="utf-8").strip().splitlines()
    dziennik.write_text("\n".join(linie[:2]) + "\n", encoding="utf-8")

    kod = main(["audit", "verify", "--config", str(katalog)])

    assert kod == 1
    assert "NIEZGODNOŚĆ (kotwica)" in capsys.readouterr().err


def test_raport_dziala_na_dzienniku_ktory_BLOKUJE_start(
    write_config, tmp_path: Path, capsys
) -> None:
    """Sedno istnienia osobnej ścieżki wglądu.

    Ten sam dziennik, który `build_audit_log` odrzuca (a więc uniemożliwia `husarz up`),
    musi dać się obejrzeć narzędziem diagnostycznym. Inaczej operator miałby platformę,
    która nie wstaje, i żadnego sposobu, by dowiedzieć się dlaczego.
    """
    from husarz.config.loader import load_config  # noqa: PLC0415
    from husarz.security.audit import build_audit_log  # noqa: PLC0415
    from husarz.security.errors import AuditError  # noqa: PLC0415

    katalog, dziennik = _instalacja(write_config, tmp_path)
    linie = dziennik.read_text(encoding="utf-8").strip().splitlines()
    wpis = json.loads(linie[1])
    wpis["action"] = "PODMIENIONE"
    linie[1] = json.dumps(wpis, ensure_ascii=False)
    dziennik.write_text("\n".join(linie) + "\n", encoding="utf-8")
    with pytest.raises(AuditError):
        build_audit_log(load_config(katalog).security)  # założenie testu: start JEST blokowany

    kod = main(["audit", "verify", "--config", str(katalog)])

    assert kod == 1
    assert "NIEZGODNOŚĆ" in capsys.readouterr().err


def test_raport_NIE_zdradza_tresci_wpisow(write_config, tmp_path: Path, capsys) -> None:
    """Wynik bywa wklejany do zgłoszeń i logów, a `detail` niesie ścieżki i referencje kont."""
    katalog, dziennik = _instalacja(write_config, tmp_path)
    linie = dziennik.read_text(encoding="utf-8").strip().splitlines()
    wpis = json.loads(linie[2])
    wpis["prev_hash"] = json.loads(linie[0])["entry_hash"]
    linie[2] = json.dumps(wpis, ensure_ascii=False)
    dziennik.write_text("\n".join(linie) + "\n", encoding="utf-8")

    main(["audit", "verify", "--config", str(katalog)])

    zebrane = capsys.readouterr()
    assert "/tajna/sciezka/operatora" not in zebrane.out + zebrane.err
    # Nośność: wpis, o który chodzi, JEST w raporcie — pomijamy tylko jego `detail`.
    assert "akcja-2" in zebrane.err


def test_wylaczony_audyt_melduje_to_wprost(write_config, tmp_path: Path, capsys) -> None:
    """„Nie ma czego sprawdzać" to inny wynik niż „sprawdzone i w porządku"."""
    katalog = write_config({"models.yaml": _MODELE, "security.yaml": "audit:\n  enabled: false\n"})

    kod = main(["audit", "verify", "--config", str(katalog)])

    assert kod == 0
    assert "WYŁĄCZONY" in capsys.readouterr().out
