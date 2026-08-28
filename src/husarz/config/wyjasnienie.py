"""Skąd wzięła się WARTOŚĆ w konfiguracji — warstwa po warstwie.

**Po co to istnieje.** Hierarchia nadpisań to ``defaults (kod) → config/*.yaml →
ENV (HUSARZ_*) → sekrety → runtime (panel)``. Na stanowisku deweloperskim odpowiedź
„dlaczego ta wartość jest taka" jest zwykle oczywista, bo warstwa jest jedna. We wdrożeniu
kontenerowym już nie: ``deploy/k8s/configmap.yaml`` nadpisuje konfigurację zmiennymi
środowiskowymi, więc plik w repozytorium może mówić co innego niż działająca instancja —
a operator patrzy na plik.

**Sekretów NIE rozwiązujemy i to jest warunek, nie ograniczenie.** Konfiguracja przechowuje
wyłącznie REFERENCJE (``env:``/``file:``/``vault:``/``sops:``/``husarz:``), a narzędzie
diagnostyczne, które by je rozwinęło, byłoby wygodnym sposobem odczytania sekretu przez
kogoś, kto ma dostęp do powłoki, ale nie do magazynu. Pokazujemy referencję dokładnie taką,
jaka stoi w konfiguracji — nigdy materiał.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from husarz.config.errors import ConfigError
from husarz.config.loader import (
    _SINGLE_FILES,
    _env_overrides,
    _load_raw_from_dir,
    resolve_config_dir,
)

#: Wartownik odróżniający „warstwa nie ustawia tego pola" od „ustawia je na ``None``".
#: Bez niego ``null`` w YAML-u (a to legalna i znacząca wartość, np. wyłączenie limitu)
#: wyglądałby jak brak wpisu.
BRAK = object()


@dataclass(frozen=True, slots=True)
class WartoscWarstwy:
    """Co dana warstwa mówi o wskazanej ścieżce.

    Attributes:
        nazwa: Nazwa warstwy do pokazania operatorowi.
        wartosc: Wartość albo :data:`BRAK`, gdy warstwa nic nie ustawia.
        zrodlo: Doprecyzowanie źródła (nazwa pliku, nazwa zmiennej) albo pusty napis.
    """

    nazwa: str
    wartosc: Any
    zrodlo: str = ""

    @property
    def ustawia(self) -> bool:
        """Czy warstwa w ogóle wypowiada się o tej ścieżce."""
        return self.wartosc is not BRAK


@dataclass(frozen=True, slots=True)
class Wyjasnienie:
    """Pełna odpowiedź na pytanie „skąd ta wartość".

    Attributes:
        sciezka: Ścieżka kropkowa, o którą pytano.
        warstwy: Warstwy w kolejności rosnącego priorytetu.
        obowiazujaca: Nazwa warstwy, której wartość obowiązuje.
        wartosc: Wartość obowiązująca.
        jest_referencja: Czy wartość wygląda na referencję do sekretu.
    """

    sciezka: str
    warstwy: tuple[WartoscWarstwy, ...]
    obowiazujaca: str
    wartosc: Any
    jest_referencja: bool


#: Schematy referencji do sekretów. Wartość zaczynająca się od któregokolwiek z nich jest
#: WSKAZANIEM na sekret, a nie samym sekretem — i tak ma zostać pokazana.
_SCHEMATY_REFERENCJI = ("env:", "file:", "vault:", "sops:", "husarz:")


def _zejdz(dane: Any, segmenty: list[str]) -> Any:
    """Schodzi po ścieżce kropkowej w zagnieżdżonych mapach.

    Args:
        dane: Struktura wejściowa (zwykle słownik z warstwy).
        segmenty: Kolejne segmenty ścieżki.

    Returns:
        Znaleziona wartość albo :data:`BRAK`, gdy ścieżka nie istnieje.
    """
    biezace: Any = dane
    for segment in segmenty:
        if not isinstance(biezace, Mapping) or segment not in biezace:
            return BRAK
        biezace = biezace[segment]
    return biezace


def _plik_sekcji(sekcja: str) -> str:
    """Nazwa pliku, z którego pochodzi sekcja najwyższego poziomu (pusta, gdy wieloplikowa)."""
    return _SINGLE_FILES.get(sekcja, "")


def wyjasnij(
    sciezka: str,
    *,
    config_dir: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    runtime_overrides: Mapping[str, Any] | None = None,
) -> Wyjasnienie:
    """Ustala, która warstwa konfiguracji dostarcza wartość dla wskazanej ścieżki.

    Args:
        sciezka: Ścieżka kropkowa, np. ``security.diagnostics.max_requests_per_minute``.
        config_dir: Katalog konfiguracji; ``None`` = z ENV albo ``./config``.
        env: Środowisko do odczytu (wstrzykiwalne w testach).
        runtime_overrides: Nadpisania runtime, jeśli są znane wołającemu.

    Returns:
        Opis warstw i wartości obowiązującej.

    Raises:
        ConfigError: Gdy ścieżka jest pusta albo katalogu konfiguracji nie da się wczytać.
    """
    # `strip()` przed filtrem, nie po: bez niego segment złożony z samych spacji przechodził
    # jako niepusty i dawał ścieżkę, która nigdzie nie istnieje — czyli odpowiedź „obowiązuje
    # wartość domyślna" na pytanie, które w ogóle nie było pytaniem.
    segmenty = [oczyszczony for s in sciezka.split(".") if (oczyszczony := s.strip())]
    if not segmenty:
        raise ConfigError("Podaj ścieżkę kropkową, np. `security.audit.integrity`.")

    import os  # noqa: PLC0415 - import lokalny: moduł ma działać także bez środowiska

    srodowisko = os.environ if env is None else env
    katalog = resolve_config_dir(config_dir, srodowisko)
    z_plikow = _load_raw_from_dir(katalog)
    z_env = _env_overrides(srodowisko)
    z_runtime = dict(runtime_overrides or {})

    warstwy = (
        WartoscWarstwy("defaults (kod)", BRAK, "schemat Pydantic"),
        WartoscWarstwy("config/*.yaml", _zejdz(z_plikow, segmenty), _plik_sekcji(segmenty[0])),
        WartoscWarstwy("ENV (HUSARZ_*)", _zejdz(z_env, segmenty), _nazwa_zmiennej(segmenty)),
        WartoscWarstwy("runtime (panel)", _zejdz(z_runtime, segmenty), ""),
    )

    # Wygrywa warstwa NAJWYŻSZA, która cokolwiek ustawia. Gdy żadna — wartość pochodzi
    # z domyślnej w schemacie, i trzeba to powiedzieć wprost, bo „nie ma tego nigdzie"
    # bywa mylone z „tego nie ma wcale".
    obowiazujaca = "defaults (kod)"
    wartosc: Any = BRAK
    for warstwa in warstwy:
        if warstwa.ustawia:
            obowiazujaca = warstwa.nazwa
            wartosc = warstwa.wartosc

    return Wyjasnienie(
        sciezka=".".join(segmenty),
        warstwy=warstwy,
        obowiazujaca=obowiazujaca,
        wartosc=wartosc,
        jest_referencja=isinstance(wartosc, str) and wartosc.startswith(_SCHEMATY_REFERENCJI),
    )


def _nazwa_zmiennej(segmenty: list[str]) -> str:
    """Buduje nazwę zmiennej środowiskowej odpowiadającej ścieżce.

    Nazwa jest PODPOWIEDZIĄ, nie odczytem: pokazuje operatorowi, którą zmienną ustawić
    (albo której szukać), także wtedy, gdy warstwa ENV nic dziś nie wnosi.

    Args:
        segmenty: Segmenty ścieżki kropkowej.

    Returns:
        Nazwa w postaci ``HUSARZ_SEKCJA__POLE``.
    """
    return "HUSARZ_" + "__".join(s.upper() for s in segmenty)
