"""Niezmienniki bezpieczeństwa zapisywalnego magazynu sekretów (``husarz:``).

Testujemy SKUTEK, nie deklarację: czy w pliku na dysku faktycznie nie ma materiału, jakie
prawa ma plik po zapisie, co się dzieje przy złym kluczu i przy podmianie wpisu. Asercja
„wywołano seal()" nie byłaby dowodem — a to właśnie ten rodzaj testu przepuścił w tym
projekcie sześć realnych wad.
"""

from __future__ import annotations

import base64
import json
import stat
from datetime import UTC, datetime
from pathlib import Path

import pytest

from husarz.core.crypto import AesGcmCipher, derive_key
from husarz.security.secret_store import (
    SCHEME,
    EncryptedFileSecretStore,
    SecretStoreError,
    build_secret_store,
)

pytestmark = pytest.mark.security

_TOKEN = "ghp_PRZYKLADOWY_TOKEN_TESTOWY_1234567890"


class _DictSecrets:
    """Dostawca sekretów ze słownika — zastępuje ENV/Vault w teście."""

    def __init__(self, dane: dict[str, str]) -> None:
        self._dane = dane

    def resolve(self, ref: str) -> str | None:
        """Zwraca wartość albo ``None`` — jak każdy dostawca sekretów."""
        return self._dane.get(ref)


def _magazyn(tmp_path: Path, klucz: str = "klucz-glowny-testowy") -> EncryptedFileSecretStore:
    return build_secret_store(
        path=tmp_path / "sekrety" / "store.json",
        key_ref="env:KLUCZ",
        secrets=_DictSecrets({"env:KLUCZ": klucz}),
        clock=lambda: datetime(2026, 8, 22, 12, 0, tzinfo=UTC),
    )


def test_zapisany_token_nie_wystepuje_jawnie_w_pliku(tmp_path: Path) -> None:
    """Najważniejszy niezmiennik: plik magazynu NIE zawiera materiału sekretu."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)

    surowe = (tmp_path / "sekrety" / "store.json").read_bytes()
    assert _TOKEN.encode("utf-8") not in surowe, "token trafił na dysk jawnym tekstem"
    # Kontrola nośności testu: nazwa wpisu JEST jawna (i tak ma być) — gdyby powyższa
    # asercja przechodziła dlatego, że czytamy nie ten plik, ta by się wywaliła.
    assert b"git/github" in surowe


def test_roundtrip_zwraca_referencje_i_wartosc(tmp_path: Path) -> None:
    """``put`` zwraca referencję, ``resolve`` odtwarza dokładnie tę samą wartość."""
    store = _magazyn(tmp_path)
    ref = store.put("git/github", _TOKEN)

    assert ref == f"{SCHEME}git/github"
    assert store.resolve(ref) == _TOKEN


def test_magazyn_przezywa_restart(tmp_path: Path) -> None:
    """Nowa instancja czyta z dysku — sekret przeżywa restart procesu."""
    _magazyn(tmp_path).put("git/gitlab", _TOKEN)

    po_restarcie = _magazyn(tmp_path)
    assert po_restarcie.resolve(f"{SCHEME}git/gitlab") == _TOKEN


def test_zly_klucz_glowny_nie_odszyfrowuje(tmp_path: Path) -> None:
    """Inny klucz główny daje ``None`` (fail-closed), nigdy śmieci ani wyjątku."""
    _magazyn(tmp_path, klucz="klucz-A").put("git/github", _TOKEN)

    obcy = _magazyn(tmp_path, klucz="klucz-B")
    assert obcy.resolve(f"{SCHEME}git/github") is None
    # Nazwa pozostaje widoczna — magazyn nie udaje, że wpisu nie ma; to klucz jest zły.
    assert obcy.names() == ["git/github"]


def test_podmiana_wpisu_pod_inna_nazwe_jest_wykrywana(tmp_path: Path) -> None:
    """AAD wiąże szyfrogram z nazwą — przeniesiony wpis się NIE odszyfruje (anti-swap)."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)
    store.put("git/gitlab", "inny-token")

    plik = tmp_path / "sekrety" / "store.json"
    dane = json.loads(plik.read_text(encoding="utf-8"))
    # Atak: podstaw szyfrogram GitHuba pod nazwę GitLaba (np. mając dostęp do pliku).
    dane["entries"]["git/gitlab"]["sealed"] = dane["entries"]["git/github"]["sealed"]
    plik.write_text(json.dumps(dane), encoding="utf-8")

    po_podmianie = _magazyn(tmp_path)
    assert po_podmianie.resolve(f"{SCHEME}git/gitlab") is None
    # A oryginalny wpis nadal działa — czyli test wykrył PODMIANĘ, a nie zepsucie pliku.
    assert po_podmianie.resolve(f"{SCHEME}git/github") == _TOKEN


def test_prawa_pliku_i_katalogu_sa_wlasciwe(tmp_path: Path) -> None:
    """Plik ``0600`` w katalogu ``0700`` — inne konto na maszynie nie przeczyta."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)

    plik = tmp_path / "sekrety" / "store.json"
    assert stat.S_IMODE(plik.stat().st_mode) == 0o600, oct(stat.S_IMODE(plik.stat().st_mode))
    katalog = stat.S_IMODE((tmp_path / "sekrety").stat().st_mode)
    assert katalog == 0o700, oct(katalog)


def test_po_zapisie_nie_zostaje_plik_tymczasowy(tmp_path: Path) -> None:
    """Zapis atomowy sprząta po sobie — inaczej szyfrogram leżałby w drugim pliku.

    Obok magazynu leży plik blokady międzyprocesowej. Sprawdzamy więc dwie rzeczy: że NIE MA
    pliku tymczasowego (tam trafiałby szyfrogram) i że plik blokady jest PUSTY — jego rolą
    jest wyłącznie istnienie i-węzła do zablokowania, nie przechowywanie czegokolwiek.
    """
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)
    store.put("git/github", "token-po-rotacji")

    pozostale = sorted(p.name for p in (tmp_path / "sekrety").iterdir())
    assert not [n for n in pozostale if n.endswith(".tmp")], pozostale
    assert pozostale == ["store.json", "store.json.lock"], pozostale
    assert (tmp_path / "sekrety" / "store.json.lock").stat().st_size == 0


def test_obcy_schemat_referencji_jest_ignorowany(tmp_path: Path) -> None:
    """Magazyn odpowiada wyłącznie za ``husarz:`` — reszta należy do innych dostawców."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)

    assert store.resolve("env:git/github") is None
    assert store.resolve("vault:git/github") is None
    assert store.resolve("git/github") is None


def test_nieznana_nazwa_daje_none(tmp_path: Path) -> None:
    """Brak wpisu daje ``None``; wyjątek zdradzałby, co w magazynie jest."""
    assert _magazyn(tmp_path).resolve(f"{SCHEME}nie-ma-takiego") is None


def test_uszkodzony_plik_nie_udaje_pustego_magazynu(tmp_path: Path) -> None:
    """Fail-closed: uszkodzony magazyn to BŁĄD, nie cichy start z zerem sekretów."""
    sciezka = tmp_path / "sekrety"
    sciezka.mkdir(mode=0o700)
    (sciezka / "store.json").write_text("{to nie jest json", encoding="utf-8")

    with pytest.raises(SecretStoreError):
        _magazyn(tmp_path)


def test_brak_klucza_glownego_blokuje_budowe(tmp_path: Path) -> None:
    """Bez rozwiązywalnego klucza magazyn NIE powstaje — nie ma trybu zapisu jawnego."""
    with pytest.raises(SecretStoreError):
        build_secret_store(path=tmp_path / "s.json", key_ref=None, secrets=_DictSecrets({}))
    with pytest.raises(SecretStoreError):
        build_secret_store(path=tmp_path / "s.json", key_ref="env:BRAK", secrets=_DictSecrets({}))


def test_pusta_wartosc_jest_odrzucana(tmp_path: Path) -> None:
    """Pusty sekret objawiłby się dopiero jako odmowa zdalnego serwisu — blokujemy tu."""
    store = _magazyn(tmp_path)
    with pytest.raises(SecretStoreError):
        store.put("git/github", "")
    with pytest.raises(SecretStoreError):
        store.put("git/github", "   ")


@pytest.mark.parametrize(
    "nazwa",
    ["", "/absolutna", "../ucieczka", "spacja w nazwie", "a" * 129],
)
def test_niepoprawne_nazwy_sa_odrzucane(tmp_path: Path, nazwa: str) -> None:
    """Nazwa trafia do referencji w configu — walidujemy ją przy zapisie."""
    with pytest.raises(SecretStoreError):
        _magazyn(tmp_path).put(nazwa, _TOKEN)


def test_usuwanie_jest_idempotentne_i_kasuje_szyfrogram(tmp_path: Path) -> None:
    """Po ``delete`` szyfrogramu nie ma w pliku — nie zostaje „miękko usunięty"."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)
    sealed = json.loads((tmp_path / "sekrety" / "store.json").read_text(encoding="utf-8"))[
        "entries"
    ]["git/github"]["sealed"]

    assert store.delete("git/github") is True
    assert store.delete("git/github") is False
    assert store.resolve(f"{SCHEME}git/github") is None
    assert sealed not in (tmp_path / "sekrety" / "store.json").read_text(encoding="utf-8")


def test_describe_nie_ujawnia_wartosci_ani_szyfrogramu(tmp_path: Path) -> None:
    """Widok dla panelu: nazwa i data, nic więcej."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)

    opis = store.describe("git/github")
    assert opis == {"name": "git/github", "created_at": "2026-08-22T12:00:00+00:00"}
    assert _TOKEN not in json.dumps(opis)
    assert store.describe("nie-ma") is None


def test_rotacja_nadpisuje_stary_szyfrogram(tmp_path: Path) -> None:
    """Ponowny ``put`` pod tą samą nazwą zastępuje wartość — stary token przestaje działać."""
    store = _magazyn(tmp_path)
    store.put("git/github", _TOKEN)
    store.put("git/github", "token-po-rotacji")

    assert store.resolve(f"{SCHEME}git/github") == "token-po-rotacji"
    assert _TOKEN.encode("utf-8") not in (tmp_path / "sekrety" / "store.json").read_bytes()


def test_ten_sam_sekret_dwa_razy_daje_rozne_szyfrogramy(tmp_path: Path) -> None:
    """Losowy nonce per zapis — inaczej równość szyfrogramów zdradzałaby równość tokenów."""
    store = _magazyn(tmp_path)
    store.put("a", _TOKEN)
    pierwszy = json.loads((tmp_path / "sekrety" / "store.json").read_text(encoding="utf-8"))[
        "entries"
    ]["a"]["sealed"]
    store.put("b", _TOKEN)
    drugi = json.loads((tmp_path / "sekrety" / "store.json").read_text(encoding="utf-8"))[
        "entries"
    ]["b"]["sealed"]

    assert pierwszy != drugi, "identyczny szyfrogram zdradza, że oba wpisy mają tę samą wartość"


def test_szyfrogram_jest_faktycznie_aes_gcm_a_nie_kodowaniem(tmp_path: Path) -> None:
    """Dowód, że to szyfrowanie, a nie base64: bez klucza treści nie da się odzyskać."""
    store = _magazyn(tmp_path, klucz="klucz-A")
    store.put("a", _TOKEN)
    sealed_b64 = json.loads((tmp_path / "sekrety" / "store.json").read_text(encoding="utf-8"))[
        "entries"
    ]["a"]["sealed"]
    sealed = base64.b64decode(sealed_b64)

    assert _TOKEN.encode("utf-8") not in sealed
    # Właściwy klucz odszyfruje; to potwierdza, że badamy ten sam materiał.
    wlasciwy = AesGcmCipher(derive_key("klucz-A"))
    assert wlasciwy.unseal(sealed, aad=b"a") == _TOKEN.encode("utf-8")
