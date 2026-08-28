"""Pobranie, weryfikacja podpisu i podmiana binarki (Etap 18p).

**To jest najwrażliwszy kod w projekcie i testy mają to odzwierciedlać.** Aktualizator
doprowadza do WYKONANIA cudzego kodu na maszynie operatora. Bez weryfikacji podpisu
przejęcie kanału wydań — albo samego konta u dostawcy — dawałoby przejęcie każdej
instalacji naraz.

Cztery własności, każda z osobnym testem, bo każda broni przed czymś innym:

* **podpis jest warunkiem, nie ostrzeżeniem** — zły podpis kończy się odmową, nie
  komunikatem obok zainstalowanej wersji;
* **weryfikacja poprzedza zapis na ścieżkę docelową** — plik, który nie przeszedł kontroli,
  nigdy nie ląduje pod nazwą, którą system uruchamia;
* **weryfikujemy PONOWNIE przed podmianą** — między pobraniem a restartem plik leży na
  dysku, a to jest czas, w którym można go podmienić;
* **każdy skok przekierowania jest sprawdzany osobno** — allowlista i pin IP obowiązują
  także po 302, bo inaczej przekierowanie byłoby drogą do dowolnego adresu.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from husarz.config.schema import HusarzConfig
from husarz.launcher.instalacja import (
    BladInstalacji,
    przygotuj,
    zastosuj_oczekujaca,
    zweryfikuj_podpis,
)

pytestmark = pytest.mark.security

BINARKA = b"\x7fELF udawana binarka husarza"


@pytest.fixture
def klucze() -> tuple[Ed25519PrivateKey, str]:
    """Para kluczy testowych: prywatny do podpisania, publiczny (PEM) do weryfikacji."""
    prywatny = Ed25519PrivateKey.generate()
    publiczny = (
        prywatny.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8")
    )
    return prywatny, publiczny


def _podpisz(prywatny: Ed25519PrivateKey, dane: bytes) -> str:
    return base64.b64encode(prywatny.sign(dane)).decode("ascii")


# --------------------------------------------------------------------------------------
# Podpis jako WARUNEK
# --------------------------------------------------------------------------------------


def test_poprawny_podpis_przechodzi(klucze) -> None:
    """Ścieżka pogodna — bez niej pozostałe testy nie dowodzą, że cokolwiek działa."""
    prywatny, publiczny = klucze

    zweryfikuj_podpis(BINARKA, _podpisz(prywatny, BINARKA), publiczny)


def test_ZMIENIONA_binarka_jest_ODRZUCANA(klucze) -> None:
    """Sedno: podpis chroni bajty, które zostaną uruchomione."""
    prywatny, publiczny = klucze
    podpis = _podpisz(prywatny, BINARKA)

    with pytest.raises(BladInstalacji, match="PODPIS SIĘ NIE ZGADZA"):
        zweryfikuj_podpis(BINARKA + b"\x00zlosliwy dopisek", podpis, publiczny)


def test_podpis_OBCYM_kluczem_jest_ODRZUCANY(klucze) -> None:
    """Przejęcie kanału wydań bez klucza podpisującego nie może wystarczyć."""
    _, publiczny = klucze
    obcy = Ed25519PrivateKey.generate()

    with pytest.raises(BladInstalacji, match="PODPIS SIĘ NIE ZGADZA"):
        zweryfikuj_podpis(BINARKA, _podpisz(obcy, BINARKA), publiczny)


@pytest.mark.parametrize(
    ("zly", "fragment"),
    [
        ("", "pusty"),
        ("   ", "pusty"),
        ("to nie jest base64!!", "base64"),
        ("====", "base64"),
    ],
)
def test_uszkodzony_plik_podpisu_jest_ODRZUCANY(klucze, zly: str, fragment: str) -> None:
    """Nieczytelny podpis to odmowa — i z komunikatem mówiącym, CO jest nie tak.

    Sprawdzamy TREŚĆ komunikatu, nie tylko fakt odmowy. Kontrola nośności pokazała, że bez
    tego test przechodzi także wtedy, gdy pusty podpis idzie do weryfikacji kryptograficznej
    i odpada tam jako „PODPIS SIĘ NIE ZGADZA". Odmowa byłaby wtedy poprawna, ale komunikat
    myliłby operatora: alarm o niezgodnym podpisie brzmi jak atak, a chodzi o pusty plik.
    """
    _, publiczny = klucze

    with pytest.raises(BladInstalacji, match=fragment):
        zweryfikuj_podpis(BINARKA, zly, publiczny)


def test_klucz_OPISANY_jako_ed25519_ale_INNEGO_typu_jest_ODRZUCANY() -> None:
    """Etykieta typu jest sprawdzana WEWNĄTRZ blobu, nie tylko w tekście przed nim.

    Format OpenSSH niesie typ klucza dwa razy: jako słowo w linii i jako pierwsze pole
    zakodowanego blobu. Gdyby sprawdzać tylko to pierwsze, klucz innego algorytmu opisany
    słowem `ssh-ed25519` zostałby przyjęty jako Ed25519 — a to jest dokładnie ta klasa
    pomyłki, która w kryptografii kończy się przyjęciem czegoś, co nie było podpisem.
    """
    import struct  # noqa: PLC0415

    from husarz.security.ed25519 import Ed25519Error, wczytaj_klucz_publiczny  # noqa: PLC0415

    def pole(dane: bytes) -> bytes:
        return struct.pack(">I", len(dane)) + dane

    blob = pole(b"ssh-rsa") + pole(b"\x00" * 32)
    podrobiony = "ssh-ed25519 " + base64.b64encode(blob).decode("ascii") + " podszywacz"

    with pytest.raises(Ed25519Error, match="nie jest kluczem Ed25519"):
        wczytaj_klucz_publiczny(podrobiony)


def test_klucz_publiczny_z_ssh_keygen_JEST_przyjmowany(tmp_path: Path) -> None:
    """Format OpenSSH — ten, który wytwarza polecenie podawane operatorowi w dokumentacji.

    Pierwotny kod czytał wyłącznie PEM i surowy base64, więc klucz wygenerowany zgodnie
    z własną instrukcją projektu zostałby odrzucony, i to z komunikatem sugerującym, że jest
    zły. Wykryto to przy wydzielaniu weryfikacji do wspólnego modułu.
    """
    import shutil  # noqa: PLC0415
    import subprocess  # noqa: PLC0415

    # Pełna ścieżka, nie sama nazwa: wołamy DOKŁADNIE to narzędzie, które ma operator.
    # Gdy go nie ma (część obrazów CI), test jest POMIJANY z podanym powodem — nigdy
    # nie udaje sukcesu, bo pomiar zaokrąglający „nie dało się sprawdzić" do „w porządku"
    # jest gorszy niż brak pomiaru.
    narzedzie = shutil.which("ssh-keygen")
    if narzedzie is None:  # pragma: no cover - zależy od obrazu systemu
        pytest.skip("brak `ssh-keygen` — nie da się wytworzyć klucza w formacie OpenSSH")
    subprocess.run(  # noqa: S603
        [narzedzie, "-t", "ed25519", "-f", str(tmp_path / "k"), "-N", "", "-C", "test"],
        capture_output=True,
        check=True,
    )
    publiczny = (tmp_path / "k.pub").read_text(encoding="utf-8")
    prywatny_pem = (tmp_path / "k").read_text(encoding="utf-8")
    prywatny = serialization.load_ssh_private_key(prywatny_pem.encode("utf-8"), password=None)

    zweryfikuj_podpis(BINARKA, base64.b64encode(prywatny.sign(BINARKA)).decode(), publiczny)


# --------------------------------------------------------------------------------------
# Staging i podmiana
# --------------------------------------------------------------------------------------


def test_plik_oczekujacy_NIE_lezy_pod_nazwa_uruchamialna(tmp_path: Path, klucze) -> None:
    """Zweryfikowana wersja czeka obok, a nie pod nazwą, którą system uruchamia."""
    prywatny, _ = klucze
    cel = tmp_path / "husarz-app"
    cel.write_bytes(b"stara wersja")

    oczekujaca = przygotuj(BINARKA, _podpisz(prywatny, BINARKA), cel)

    assert oczekujaca.name == "husarz-app.new"
    assert cel.read_bytes() == b"stara wersja", "binarka podmieniona PRZED restartem"
    assert oczekujaca.with_name("husarz-app.new.sig").is_file(), "podpis nie został zachowany"


def test_podmiana_przy_starcie_instaluje_i_zachowuje_poprzednia(tmp_path: Path, klucze) -> None:
    """Poprzednia wersja zostaje jako `.old` — gdyby nowa nie wstała, jest do czego wrócić."""
    prywatny, publiczny = klucze
    cel = tmp_path / "husarz-app"
    cel.write_bytes(b"stara wersja")
    przygotuj(BINARKA, _podpisz(prywatny, BINARKA), cel)

    komunikat = zastosuj_oczekujaca(cel, publiczny)

    assert cel.read_bytes() == BINARKA
    assert (tmp_path / "husarz-app.old").read_bytes() == b"stara wersja"
    assert not (tmp_path / "husarz-app.new").exists()
    assert "Zainstalowano" in komunikat


def test_podmiana_WERYFIKUJE_PONOWNIE_przed_instalacją(tmp_path: Path, klucze) -> None:
    """Między pobraniem a restartem plik leży na dysku — to jest czas na podmianę.

    Test podmienia zawartość pliku oczekującego PO jego przygotowaniu, czyli dokładnie tak,
    jak zrobiłby to ktoś z dostępem do katalogu. Bez powtórnej weryfikacji instalacja
    przeszłaby, bo przy pobraniu podpis się zgadzał.
    """
    prywatny, publiczny = klucze
    cel = tmp_path / "husarz-app"
    cel.write_bytes(b"stara wersja")
    oczekujaca = przygotuj(BINARKA, _podpisz(prywatny, BINARKA), cel)

    oczekujaca.write_bytes(b"\x7fELF PODMIENIONA po weryfikacji")

    with pytest.raises(BladInstalacji, match="PODPIS SIĘ NIE ZGADZA"):
        zastosuj_oczekujaca(cel, publiczny)
    assert cel.read_bytes() == b"stara wersja", "zainstalowano plik bez ważnego podpisu"


def test_plik_oczekujacy_BEZ_podpisu_nie_jest_instalowany(tmp_path: Path, klucze) -> None:
    """Usunięcie pliku podpisu nie może być drogą do pominięcia weryfikacji."""
    prywatny, publiczny = klucze
    cel = tmp_path / "husarz-app"
    cel.write_bytes(b"stara wersja")
    przygotuj(BINARKA, _podpisz(prywatny, BINARKA), cel)
    (tmp_path / "husarz-app.new.sig").unlink()

    with pytest.raises(BladInstalacji, match="nie ma podpisu"):
        zastosuj_oczekujaca(cel, publiczny)
    assert cel.read_bytes() == b"stara wersja"


def test_brak_oczekujacej_wersji_sprzata_poprzednia(tmp_path: Path, klucze) -> None:
    """Kopia `.old` znika dopiero wtedy, gdy nowa wersja się uruchomiła."""
    _, publiczny = klucze
    cel = tmp_path / "husarz-app"
    cel.write_bytes(BINARKA)
    (tmp_path / "husarz-app.old").write_bytes(b"stara wersja")

    assert zastosuj_oczekujaca(cel, publiczny) == ""
    assert not (tmp_path / "husarz-app.old").exists()


# --------------------------------------------------------------------------------------
# Pobieranie: allowlista i przekierowania
# --------------------------------------------------------------------------------------


def _config(*hosty: str) -> HusarzConfig:
    return HusarzConfig.model_validate(
        {
            "models": {"default": "m", "registry": {"m": {"backend": "mock", "model": "m"}}},
            "update": {
                "enabled": True,
                "repository": "a/b",
                "sources": list(hosty) or ["api.github.com"],
            },
        }
    )


def test_pobranie_z_hosta_SPOZA_allowlisty_jest_ODRZUCANE() -> None:
    """Adres pobrania bywa na innym hoście niż API — i on też musi być dozwolony."""
    from husarz.launcher.instalacja import pobierz  # noqa: PLC0415

    with pytest.raises(BladInstalacji, match="nie jest na liście `update.sources`"):
        pobierz(
            "https://zlosliwy.example/husarz-app-linux",
            config=_config("api.github.com"),
            limit=1024,
        )


def test_PRZEKIEROWANIE_poza_allowliste_jest_ODRZUCANE(monkeypatch) -> None:
    """Sedno kontroli skoków: 302 nie może wyprowadzić poza listę dozwolonych hostów.

    Reszta projektu ustawia `follow_redirects=False` właśnie dlatego, że przekierowanie
    omija walidację i pin IP. Tutaj musimy je obsłużyć (serwer wydań przekierowuje na
    magazyn plików), więc każdy skok sprawdzamy osobno — inaczej byłaby to dziura dokładnie
    w miejscu, w którym najbardziej boli.
    """
    import httpx  # noqa: PLC0415

    from husarz.launcher import instalacja  # noqa: PLC0415

    class _Odpowiedz:
        status_code = 302
        headers = {"location": "https://zlosliwy.example/plik"}
        content = b""

    class _Klient:
        def __init__(self, **kwargs: object) -> None: ...
        def __enter__(self) -> _Klient:
            return self

        def __exit__(self, *args: object) -> None: ...
        def get(self, *args: object, **kwargs: object) -> _Odpowiedz:
            return _Odpowiedz()

    monkeypatch.setattr(httpx, "Client", _Klient)

    with pytest.raises(BladInstalacji, match="nie jest na liście `update.sources`"):
        instalacja.pobierz(
            "https://api.github.com/plik",
            config=_config("api.github.com"),
            limit=1024,
            resolve=lambda h: ["140.82.121.4"],
        )
