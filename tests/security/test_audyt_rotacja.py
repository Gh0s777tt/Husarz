"""Rotacja klucza HMAC dziennika audytu i blokująca kontrola integralności.

**Skąd ten plik.** ROADMAP notowała dwie luki obok siebie i obie dotyczyły tego samego:
dziennika, któremu w praktyce nie da się ufać na dłuższą metę.

1. *Rotacja klucza zachowywała się jak włączenie HMAC po raz pierwszy* — czyli odmową
   startu. Jedynym wyjściem było zarchiwizowanie dziennika i założenie nowego. Dziennik
   audytu, który trzeba wyrzucić przy każdej wymianie klucza, przestaje być dziennikiem
   audytu: historia znika dokładnie wtedy, gdy operator robi coś zalecanego.
2. *Uszkodzony łańcuch BEZ klucza HMAC nie blokował startu.* Instalacja szła dalej,
   a fakt był widoczny wyłącznie jako `verified: false` w odpowiedzi API — czyli tam,
   gdzie nikt nie patrzy, dopóki nie szuka.

Sedno rotacji nie leży w samym doborze klucza po etykiecie (to księgowość), lecz w tym,
czy klucz WYCOFANY przestaje cokolwiek uwierzytelniać. Test
`test_wycofany_klucz_NIE_dopisze_sie_do_koncowki` jest tu najważniejszy: bez reguły
niemalejącego pokolenia rotacja byłaby pozorna, bo stary klucz nadal podpisywałby nowe wpisy.
"""

from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from husarz.config.errors import ConfigError
from husarz.config.loader import load_config
from husarz.config.schema import AuditConfig, AuditIntegrity, SecurityConfig
from husarz.security.audit import AuditLog, _payload, build_audit_log
from husarz.security.errors import AuditError

pytestmark = pytest.mark.security

STARY = b"klucz-pokolenia-pierwszego"
NOWY = b"klucz-pokolenia-drugiego"


class _Sekrety:
    """Dostawca sekretów wyłącznie na potrzeby testu (mapa referencja -> materiał)."""

    def __init__(self, mapa: dict[str, bytes]) -> None:
        self._mapa = mapa

    def resolve(self, ref: str) -> str | None:
        wartosc = self._mapa.get(ref)
        return None if wartosc is None else wartosc.decode("utf-8")


SEKRETY = _Sekrety({"env:STARY": STARY, "env:NOWY": NOWY})


def _config(sciezka: Path, **audyt: object) -> SecurityConfig:
    """Konfiguracja bezpieczeństwa z dziennikiem pod wskazaną ścieżką."""
    return SecurityConfig(audit=AuditConfig(path=sciezka, **audyt))  # type: ignore[arg-type]


def _dziennik_pokolenia_pierwszego(tmp_path: Path, wpisow: int = 2) -> Path:
    """Zakłada dziennik podpisany STARYM kluczem, bez etykiety pokolenia."""
    sciezka = tmp_path / "audit.log"
    log = build_audit_log(_config(sciezka, hmac_key_ref="env:STARY"), secrets=SEKRETY)
    for i in range(wpisow):
        log.record("api", f"akcja-{i}", {"i": i})
    return sciezka


def _po_rotacji(sciezka: Path) -> SecurityConfig:
    """Konfiguracja po rotacji: nowy klucz bieżący, stary wyłącznie do weryfikacji."""
    return _config(
        sciezka,
        hmac_key_ref="env:NOWY",
        hmac_key_id="2026-08",
        hmac_verify_keys=[{"id": "", "ref": "env:STARY"}],
    )


# --------------------------------------------------------------------------------------
# Rotacja
# --------------------------------------------------------------------------------------


def test_rotacja_zachowuje_weryfikowalnosc_calej_historii(tmp_path: Path) -> None:
    """Po wymianie klucza stare wpisy nadal się weryfikują — to jest cel całej zmiany."""
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path)
    stary = build_audit_log(_config(sciezka, hmac_key_ref="env:STARY"), secrets=SEKRETY)
    przed = len(stary.entries)

    log = build_audit_log(_po_rotacji(sciezka), secrets=SEKRETY)

    assert log.verify() is True
    # Historia jest na miejscu, a nie „wyczyszczona i zaczęta od nowa”.
    assert len(log.entries) > przed
    assert [e.key_id for e in log.entries[:przed]] == [""] * przed


def test_rotacja_bez_podania_starego_klucza_ODMAWIA_startu(tmp_path: Path) -> None:
    """Sama wymiana `hmac_key_ref` bez wpisania poprzednika = fail-closed, jak dotąd."""
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path)

    with pytest.raises(AuditError) as blad:
        build_audit_log(
            _config(sciezka, hmac_key_ref="env:NOWY", hmac_key_id="2026-08"), secrets=SEKRETY
        )

    # Komunikat ma prowadzić do rozwiązania, a nie tylko oznajmiać porażkę.
    assert "hmac_verify_keys" in str(blad.value)


def test_znacznik_rotacji_powstaje_RAZ(tmp_path: Path) -> None:
    """Znacznik zamyka okno bez wpisu nowego pokolenia; kolejne starty go nie powielają."""
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path)

    pierwszy = build_audit_log(_po_rotacji(sciezka), secrets=SEKRETY)
    po_pierwszym = len(pierwszy.entries)
    drugi = build_audit_log(_po_rotacji(sciezka), secrets=SEKRETY)

    assert pierwszy.entries[-1].action == "audit.key_rotated"
    assert pierwszy.entries[-1].key_id == "2026-08"
    assert len(drugi.entries) == po_pierwszym, "drugi start dopisał kolejny znacznik"
    # Znacznik nie może nieść materiału klucza — wyłącznie etykiety nadane przez operatora.
    assert set(pierwszy.entries[-1].detail) == {"poprzednie_pokolenie", "biezace_pokolenie"}


def test_wycofany_klucz_NIE_dopisze_sie_do_koncowki(tmp_path: Path) -> None:
    """Sedno rotacji: stary klucz przestaje uwierzytelniać cokolwiek nowego.

    Napastnik ma WYŁĄCZNIE klucz wycofany (bo po to się rotuje — zakładamy jego wyciek).
    Podrabia wpis oznaczony starym pokoleniem i aktualizuje kotwicę, żeby nie zdradziła
    dopisku. Bez reguły niemalejącego pokolenia taki dziennik przechodziłby weryfikację.
    """
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path)
    kotwica = sciezka.with_name(sciezka.name + ".kotwica")
    build_audit_log(_po_rotacji(sciezka), secrets=SEKRETY)

    linie = sciezka.read_text(encoding="utf-8").strip().splitlines()
    prev = json.loads(linie[-1])["entry_hash"]
    czas = "2026-08-28T12:00:00+00:00"
    payload = _payload(czas, "api", "zatarcie.sladow", {}, None, prev, "", "")
    podrobiony = {
        "timestamp": czas,
        "actor": "api",
        "action": "zatarcie.sladow",
        "detail": {},
        "roe_ref": None,
        "prev_hash": prev,
        "entry_hash": hmac.new(STARY, payload.encode("utf-8"), hashlib.sha256).hexdigest(),
        "principal": "",
        "key_id": "",
    }
    with sciezka.open("a", encoding="utf-8") as uchwyt:
        uchwyt.write(json.dumps(podrobiony, ensure_ascii=False) + "\n")
    kotwica.write_text(
        json.dumps({"wpisow": len(linie) + 1, "skrot": podrobiony["entry_hash"]}), encoding="utf-8"
    )

    log = AuditLog.load(
        sciezka, hmac_key=NOWY, key_id="2026-08", verify_keys=[("", STARY)], anchor_path=kotwica
    )
    assert log.verify() is False, "wycofany klucz nadal uwierzytelnia nowe wpisy"
    # Kontrola wbudowana w test: podrobiony wpis jest kryptograficznie POPRAWNY pod starym
    # kluczem, więc odrzuca go wyłącznie reguła pokoleń, a nie zwykła niezgodność skrótu.
    # Bez tej asercji test przechodziłby też wtedy, gdyby podróbka była zwyczajnie zepsuta.
    assert log._skrot(payload, STARY) == podrobiony["entry_hash"]
    assert log.entries[-1].prev_hash == log.entries[-2].entry_hash, "łańcuch jest ciągły"


def test_nieznana_etykieta_pokolenia_uniewaznia_weryfikacje(tmp_path: Path) -> None:
    """„Nie mam czym sprawdzić” nie zaokrągla się do „w porządku”.

    Wpis jest tu POPRAWNIE podpisany kluczem, który mamy — nieznana jest wyłącznie jego
    etykieta. To rozróżnienie jest całym sensem testu: pierwsza wersja podmieniała samą
    etykietę w gotowym wpisie, przez co skrót przestawał pasować i weryfikacja padała
    z powodu niezgodności skrótu, a nie z powodu nieznanego pokolenia. Test przechodził,
    ale nie badał tego, co miał badać — wykryła to dopiero kontrola nośności.
    """
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path, wpisow=1)
    czas = "2026-08-28T12:00:00+00:00"
    obca = "pokolenie-ktorego-nie-znamy"
    prev = json.loads(sciezka.read_text(encoding="utf-8").strip().splitlines()[-1])["entry_hash"]
    payload = _payload(czas, "api", "obca-etykieta", {}, None, prev, "", obca)
    wpis = {
        "timestamp": czas,
        "actor": "api",
        "action": "obca-etykieta",
        "detail": {},
        "roe_ref": None,
        "prev_hash": prev,
        "entry_hash": hmac.new(STARY, payload.encode("utf-8"), hashlib.sha256).hexdigest(),
        "principal": "",
        "key_id": obca,
    }
    with sciezka.open("a", encoding="utf-8") as uchwyt:
        uchwyt.write(json.dumps(wpis, ensure_ascii=False) + "\n")

    log = AuditLog.load(sciezka, hmac_key=STARY)

    assert log.verify() is False
    # Nośność wbudowana: skrót JEST poprawny dla klucza, którym dysponujemy, więc
    # weryfikację unieważnia wyłącznie nieznana etykieta.
    assert log._skrot(payload, STARY) == wpis["entry_hash"]


def test_przeetykietowanie_wpisu_uniewaznia_go(tmp_path: Path) -> None:
    """`key_id` jest ZWIĄZANY ze skrótem, więc nie da się „awansować" wpisu do nowszego
    pokolenia.

    Ma znaczenie tylko wtedy, gdy dwa pokolenia dzielą materiał klucza — w innym wypadku
    zmiana etykiety i tak zmienia klucz, którym liczony jest skrót. `build_audit_log`
    taką konfigurację odrzuca (patrz test niżej), więc jest to obrona w głąb: gdyby
    tamta kontrola kiedyś zniknęła, ta nadal działa. Dlatego dziennik budujemy tu wprost,
    z pominięciem walidacji konfiguracji.
    """
    sciezka = tmp_path / "audit.log"
    log = AuditLog(path=sciezka, hmac_key=STARY, key_id="")
    log.record("api", "stary-wpis")
    nowszy = AuditLog(path=sciezka, hmac_key=STARY, key_id="k2", verify_keys=[("", STARY)])
    nowszy._wczytaj(sciezka)
    nowszy.record("api", "nowy-wpis")
    assert nowszy.verify() is True, "założenie testu"

    sprawdzany = AuditLog.load(sciezka, hmac_key=STARY, key_id="k2", verify_keys=[("", STARY)])
    sprawdzany._entries[0] = dataclasses.replace(sprawdzany._entries[0], key_id="k2")

    assert sprawdzany.verify() is False


def test_dwa_pokolenia_o_tym_samym_kluczu_sa_ODRZUCANE(tmp_path: Path) -> None:
    """Rotacja na ten sam materiał jest rotacją pozorną — i schemat jej nie wychwyci.

    Schemat widzi wyłącznie REFERENCJE, a dwie różne referencje mogą wskazywać ten sam
    sekret. Sprawdzenie musi więc nastąpić po ich rozwiązaniu.
    """
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path, wpisow=1)
    sekrety = _Sekrety({"env:STARY": STARY, "env:INNA_NAZWA_TEGO_SAMEGO": STARY})

    with pytest.raises(AuditError, match="TEN SAM materiał"):
        build_audit_log(
            _config(
                sciezka,
                hmac_key_ref="env:STARY",
                hmac_key_id="k2",
                hmac_verify_keys=[{"id": "", "ref": "env:INNA_NAZWA_TEGO_SAMEGO"}],
            ),
            secrets=sekrety,
        )


def test_dziennik_sprzed_pola_key_id_nadal_sie_weryfikuje(tmp_path: Path) -> None:
    """Zgodność wstecz: dodanie kolumny nie może wyglądać jak manipulacja historią."""
    sciezka = _dziennik_pokolenia_pierwszego(tmp_path)
    linie = sciezka.read_text(encoding="utf-8").strip().splitlines()
    # Plik sprzed Etapu 18 nie ma pola `key_id` w ogóle.
    stare = [{k: v for k, v in json.loads(w).items() if k != "key_id"} for w in linie]
    assert all("key_id" not in w for w in stare), "założenie testu"
    sciezka.write_text(
        "\n".join(json.dumps(w, ensure_ascii=False) for w in stare) + "\n", encoding="utf-8"
    )

    assert AuditLog.load(sciezka, hmac_key=STARY).verify() is True


# --------------------------------------------------------------------------------------
# Blokująca kontrola integralności
# --------------------------------------------------------------------------------------


def _uszkodz(sciezka: Path) -> None:
    """Podmienia treść pierwszego wpisu, zostawiając jego skrót — czyli psuje łańcuch."""
    linie = sciezka.read_text(encoding="utf-8").strip().splitlines()
    wpis = json.loads(linie[0])
    wpis["action"] = "podmienione"
    linie[0] = json.dumps(wpis, ensure_ascii=False)
    sciezka.write_text("\n".join(linie) + "\n", encoding="utf-8")


def test_integralnosc_blokujaca_zatrzymuje_start_BEZ_klucza_hmac(tmp_path: Path) -> None:
    """Luka z ROADMAP: bez HMAC uszkodzony łańcuch przepuszczał start."""
    sciezka = tmp_path / "audit.log"
    log = AuditLog(path=sciezka)
    log.record("api", "start")
    log.record("api", "czat")
    _uszkodz(sciezka)

    with pytest.raises(AuditError) as blad:
        build_audit_log(_config(sciezka, integrity=AuditIntegrity.BLOCKING))

    # Komunikat MUSI mówić, czego kontrola bez klucza nie potrafi — inaczej operator
    # uzna wynik za dowód podmiany, którym on nie jest.
    assert "nie odróżnia" in str(blad.value)


def test_integralnosc_warn_nie_zatrzymuje_startu(tmp_path: Path) -> None:
    """Drugi biegun przełącznika — bez niego test wyżej nie dowodzi, że to on rozstrzyga."""
    sciezka = tmp_path / "audit.log"
    AuditLog(path=sciezka).record("api", "start")
    _uszkodz(sciezka)

    log = build_audit_log(_config(sciezka, integrity=AuditIntegrity.WARN))

    assert log.verify() is False


def test_wartosc_domyslna_jest_blokujaca(tmp_path: Path) -> None:
    """Instalacja, która niczego nie ustawia, ma być fail-closed — to sedno zmiany."""
    sciezka = tmp_path / "audit.log"
    AuditLog(path=sciezka).record("api", "start")
    _uszkodz(sciezka)

    with pytest.raises(AuditError):
        build_audit_log(_config(sciezka))


_MODELE = "default: m\nregistry:\n  m:\n    backend: mock\n    model: testowy\n"


@pytest.mark.parametrize("profil", ["prod", "airgap"])
def test_profile_nieodwolalne_wymagaja_blokujacej_integralnosci(write_config, profil: str) -> None:
    """W prod/airgap dziennik doradczy nie wystarcza."""
    katalog = write_config(
        {
            "models.yaml": _MODELE,
            "husarz.yaml": f"profile: {profil}\n",
            "security.yaml": "egress:\n  default_policy: deny\naudit:\n  integrity: warn\n",
        }
    )

    with pytest.raises(ConfigError) as blad:
        load_config(katalog)

    tresc = str(blad.value)
    assert "blokującej kontroli integralności" in tresc, tresc
    assert profil in tresc, "komunikat musi nazwać profil, który stawia to wymaganie"


def test_profil_dev_dopuszcza_warn(write_config) -> None:
    """Bez tej asercji test wyżej przechodziłby też, gdyby `warn` było odrzucane wszędzie."""
    katalog = write_config({"models.yaml": _MODELE, "security.yaml": "audit:\n  integrity: warn\n"})

    config = load_config(katalog)

    assert config.security.audit.wymusza_integralnosc is False


def test_prod_z_kluczem_hmac_startuje_bo_wartosc_domyslna_juz_blokuje(write_config) -> None:
    """Konfiguracja produkcyjna z kluczem HMAC nie potrzebuje niczego dopisywać.

    Test nosił wcześniej nazwę `..._przechodzi_na_auto` i sprawdzał stan `auto`, który
    z tego wyliczenia wypadł. Przechodził wtedy z niewłaściwego powodu — nie dlatego, że
    ustawiono klucz, lecz dlatego, że wartością domyślną jest `blocking`. Zgłoszenie
    z przeglądu; nazwa i uzasadnienie doprowadzone do stanu faktycznego.
    """
    katalog = write_config(
        {
            "models.yaml": _MODELE,
            "husarz.yaml": "profile: prod\n",
            "security.yaml": (
                "egress:\n  default_policy: deny\naudit:\n  hmac_key_ref: env:KLUCZ\n"
            ),
        }
    )

    config = load_config(katalog)

    assert config.security.audit.integrity is AuditIntegrity.BLOCKING
    assert config.security.audit.wymusza_integralnosc is True


def test_ten_sam_klucz_pod_dwiema_etykietami_jest_ODRZUCANY_przez_schemat() -> None:
    """Pozorna rotacja: klucz bieżący i historyczny o tej samej etykiecie.

    Luka pokrycia wykryta przeglądem: walidator istniał, ale nie miał ani jednego testu.
    """
    with pytest.raises(ValueError, match="powtarza etykietę"):
        AuditConfig(
            hmac_key_ref="env:NOWY",
            hmac_key_id="k1",
            hmac_verify_keys=[{"id": "k1", "ref": "env:STARY"}],  # type: ignore[list-item]
        )
    with pytest.raises(ValueError, match="powtórzone etykiety"):
        AuditConfig(
            hmac_key_ref="env:NOWY",
            hmac_verify_keys=[  # type: ignore[list-item]
                {"id": "a", "ref": "env:X"},
                {"id": "a", "ref": "env:Y"},
            ],
        )


@pytest.mark.parametrize(
    ("ref", "fragment"),
    [
        ("husarz:klucz-audytu", "husarz"),
        ("to-nie-jest-referencja-tylko-material", "ZEWNĘTRZNEGO"),
    ],
)
def test_klucz_historyczny_musi_byc_REFERENCJA_zewnetrzna(ref: str, fragment: str) -> None:
    """Ten sam zakaz, co dla klucza bieżącego — i on też nie miał testu.

    Schemat `husarz:` jest zabroniony, bo magazyn Husarza należy do systemu, którego
    dziennik ma pilnować; materiał klucza w konfiguracji jest zabroniony zawsze.
    """
    with pytest.raises(ValueError, match=fragment):
        AuditConfig(hmac_key_ref="env:NOWY", hmac_verify_keys=[{"id": "s", "ref": ref}])  # type: ignore[list-item]
