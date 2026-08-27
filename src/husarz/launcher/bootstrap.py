"""Pobranie brakujących wag modeli — z ekranem zgody i rozmiarem PRZED pobraniem.

**Po co ten moduł istnieje.** Diagnoza (`husarz doctor`) kończy się ustaleniem w rodzaju
„Silnik odpowiada, ale NIE MA modelu 'bielik-11b-v3.0-instruct'". Operator wie już, co jest
nie tak — i musi to naprawić poleceniem spoza Husarza, z dokumentacji, na własną rękę.
Ten moduł domyka pętlę: proponuje pobranie dokładnie tych modeli, których brakuje.

**Czego ten moduł NIE robi — i to jest decyzja, nie brak.**

1. **Nie pobiera ani nie instaluje SILNIKA.** Wagi ściąga silnik operatora (``POST /api/pull``
   do Ollamy); Husarz jedynie o to prosi. Dzięki temu nigdy nie dotykamy cudzego kodu
   wykonywalnego, sum kontrolnych binarek ani ścieżek instalacyjnych per system. Instalacja
   silnika należy do menedżera pakietów — patrz ``ollama/README.md``.
2. **Nie pobiera niczego bez jawnej zgody.** Zgoda jest udzielana na KONKRETNĄ listę modeli
   o ZNANYM rozmiarze, a domyślną odpowiedzią jest odmowa.
3. **Nie działa domyślnie.** ``bootstrap.enabled`` jest wyłączone w dostarczonej konfiguracji.

**Rozmiar PRZED pobraniem, nie w trakcie.** „Ekran zgody podający liczbę GB" byłby fikcją,
gdyby liczbę poznawać dopiero ze strumienia pobierania — bajty już by leciały. Czytamy więc
MANIFEST z rejestru: zmierzone 857 bajtów metadanych dla ``qwen2.5-coder:1.5b``, z których
wynika dokładny rozmiar 0,986 GB (zgadza się z „≈1 GB" w dokumentacji Ollamy). Dopiero po
zgodzie prosimy silnik o wagi.

**Dwie allowlisty, nie jedna.** Zapytanie o manifest przechodzi przez ``bootstrap.sources``,
a NIE przez ``security.egress.allowlist``. Zgoda na odczytanie rozmiaru modelu nie może po
cichu otwierać tej domeny narzędziu ``web``, wtyczkom MCP ani agentom — to byłoby
rozszczelnienie deny-all tylnymi drzwiami.

**Profil airgap: twarda odmowa.** Nie ciche pominięcie i nie „spróbuję, może przejdzie".
Airgap znaczy brak ruchu wychodzącego; komenda mówi to wprost i podaje drogę ręczną.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from husarz.config.schema import HusarzConfig, Profile
from husarz.launcher.doctor import BrakujacyModel
from husarz.ssrf import HostResolver, build_pinned_target, default_resolve

# Ile bajtów odpowiedzi manifestu jesteśmy gotowi wczytać. Manifest to metadane (setki
# bajtów); limit chroni przed serwerem, który w odpowiedzi na zapytanie o rozmiar
# przysyła strumień bez końca.
_LIMIT_MANIFESTU = 256 * 1024

# Sekundy na odpowiedź rejestru. Krótko: to metadane, nie wagi.
_TIMEOUT_MANIFESTU = 10.0


@dataclass(frozen=True, slots=True)
class RozmiarModelu:
    """Rozmiar modelu odczytany z manifestu, przed pobraniem czegokolwiek.

    Attributes:
        bajty: Suma rozmiarów warstw.
        warstw: Ile warstw składa się na model (do komunikatu; wagi to zwykle jedna z nich).
    """

    bajty: int
    warstw: int

    @property
    def gigabajty(self) -> float:
        """Rozmiar w GB (10^9 B — tak liczą go rejestry modeli i dokumentacja Ollamy)."""
        return self.bajty / 1_000_000_000


@dataclass(frozen=True, slots=True)
class Pozycja:
    """Jedna pozycja planu pobrania: czego brakuje i ile to waży.

    Attributes:
        brak: Model zgłoszony przez diagnozę jako nieobecny u dostawcy.
        rozmiar: Rozmiar z manifestu; ``None``, gdy nie udało się go ustalić.
        powod_braku_rozmiaru: Dlaczego rozmiaru nie ma. Pusty, gdy jest.
    """

    brak: BrakujacyModel
    rozmiar: RozmiarModelu | None
    powod_braku_rozmiaru: str = ""

    @property
    def pobieralna(self) -> bool:
        """Czy tę pozycję wolno w ogóle zaproponować do pobrania.

        Bez znanego rozmiaru zgoda nie byłaby zgodą opartą na faktach, więc pozycja jest
        pokazywana operatorowi WRAZ Z POWODEM, ale nie wchodzi do pobierania.
        """
        return self.rozmiar is not None


@runtime_checkable
class ZrodloWag(Protocol):
    """Dostęp do rejestru modeli i do silnika — wstrzykiwany, żeby moduł był testowalny offline.

    Tak samo jak sondy diagnozy: wszystko, co dotyka sieci, przechodzi przez ten protokół.
    Bez tego każdy test pobierania musiałby ruszyć gigabajty albo udawać, że tego nie robi.
    """

    def rozmiar(self, nazwa: str) -> tuple[RozmiarModelu | None, str]:
        """Czyta rozmiar modelu z manifestu rejestru.

        Args:
            nazwa: Nazwa modelu u dostawcy (np. ``qwen2.5-coder:7b``).

        Returns:
            Para (rozmiar albo ``None``, powód braku). Powód jest pusty, gdy rozmiar jest.
        """
        ...

    def pobierz(self, endpoint: str, nazwa: str, postep: Callable[[str], None]) -> str:
        """Prosi SILNIK o pobranie wag. Husarz sam ich nie ściąga.

        Args:
            endpoint: Endpoint silnika z konfiguracji modelu.
            nazwa: Nazwa modelu u dostawcy.
            postep: Wywoływane liniami postępu (pobieranie trwa minuty — cisza wygląda
                jak zawieszenie).

        Returns:
            Pusty łańcuch przy powodzeniu, komunikat błędu w przeciwnym razie.
        """
        ...


class OdmowaBootstrapu(Exception):
    """Bootstrap nie może się wykonać — z powodu, który operator ma zobaczyć wprost."""


def sprawdz_dopuszczalnosc(config: HusarzConfig) -> None:
    """Sprawdza, czy pobieranie wag jest w tej instalacji w ogóle dopuszczalne.

    Kolejność kontroli jest celowa: najpierw profil, potem włącznik. Operator w trybie
    airgap ma usłyszeć, że to profil zabrania — a nie że „wystarczy włączyć bootstrap",
    bo włączenie i tak nic by nie dało, a sugerowałoby, że da się obejść politykę.

    Args:
        config: Wczytana konfiguracja.

    Raises:
        OdmowaBootstrapu: Gdy profil albo konfiguracja nie pozwalają pobierać.
    """
    if config.platform.profile is Profile.AIRGAP:
        raise OdmowaBootstrapu(
            "Profil airgap: pobieranie wag jest zabronione z założenia (brak ruchu "
            "wychodzącego). Przenieś model ręcznie na maszynę i zaimportuj go w silniku — "
            "instrukcja w ollama/README.md. Włączenie `bootstrap.enabled` tego NIE zmienia."
        )
    if not config.bootstrap.enabled:
        raise OdmowaBootstrapu(
            "Pobieranie wag jest wyłączone (`bootstrap.enabled: false` w config/bootstrap.yaml). "
            "To stan domyślny: Husarz nie sięga do sieci, dopóki operator tego nie włączy."
        )


def zbuduj_plan(braki: Sequence[BrakujacyModel], zrodlo: ZrodloWag) -> list[Pozycja]:
    """Zamienia listę braków w plan z rozmiarami — PRZED jakimkolwiek pobieraniem.

    Args:
        braki: Modele zgłoszone przez diagnozę jako nieobecne u dostawcy.
        zrodlo: Dostęp do rejestru.

    Returns:
        Plan w kolejności wejściowej. Pozycje bez rozmiaru zostają w planie (operator ma
        zobaczyć, czego NIE da się pobrać i dlaczego), ale nie są pobieralne.
    """
    plan: list[Pozycja] = []
    for brak in braki:
        rozmiar, powod = zrodlo.rozmiar(brak.nazwa)
        plan.append(Pozycja(brak=brak, rozmiar=rozmiar, powod_braku_rozmiaru=powod))
    return plan


def sformatuj_plan(plan: Sequence[Pozycja]) -> list[str]:
    """Buduje ekran zgody: co, dla kogo, ile waży i ile razem.

    Args:
        plan: Wynik :func:`zbuduj_plan`.

    Returns:
        Linie do wypisania (bez znaku nowej linii).
    """
    linie: list[str] = []
    for pozycja in plan:
        brak = pozycja.brak
        gdzie = ", ".join(brak.role)
        if pozycja.rozmiar is None:
            linie.append(f"[--] {brak.nazwa} ({gdzie}) — NIE DO POBRANIA")
            linie.append(f"     powód: {pozycja.powod_braku_rozmiaru}")
            continue
        linie.append(
            f"[  ] {brak.nazwa} ({gdzie}) — {pozycja.rozmiar.gigabajty:.2f} GB "
            f"→ {brak.endpoint}"
        )
    do_pobrania = [p for p in plan if p.pobieralna]
    if do_pobrania:
        suma = sum(p.rozmiar.gigabajty for p in do_pobrania if p.rozmiar is not None)
        linie.append("")
        linie.append(
            f"RAZEM: {len(do_pobrania)} model(e) do pobrania, {suma:.2f} GB. "
            f"Pobiera SILNIK pod wskazanym adresem, nie Husarz."
        )
    return linie


def wykonaj(plan: Sequence[Pozycja], zrodlo: ZrodloWag, postep: Callable[[str], None]) -> list[str]:
    """Pobiera pozycje planu. Wywoływane WYŁĄCZNIE po zgodzie operatora.

    Args:
        plan: Wynik :func:`zbuduj_plan`.
        zrodlo: Dostęp do silnika.
        postep: Wywoływane liniami postępu.

    Returns:
        Komunikaty błędów (pusta lista = wszystko się powiodło).
    """
    bledy: list[str] = []
    for pozycja in plan:
        if not pozycja.pobieralna:
            continue
        brak = pozycja.brak
        postep(f"  … proszę silnik {brak.endpoint} o pobranie '{brak.nazwa}'")
        blad = zrodlo.pobierz(brak.endpoint, brak.nazwa, postep)
        if blad:
            bledy.append(f"{brak.nazwa}: {blad}")
    return bledy


class RejestrISilnik:
    """Realne źródło: manifest z rejestru + pobieranie przez silnik operatora.

    **Bramka egress obowiązuje tu inaczej niż w reszcie systemu i to jest celowe.**
    Zapytanie o manifest sprawdzamy wobec ``bootstrap.sources``, nie wobec
    ``security.egress.allowlist`` — patrz docstring modułu. Adres samego silnika to
    infrastruktura operatora i przechodzi tę samą kontrolę co ruch routera.

    Args:
        config: Konfiguracja (rejestr, allowlista, polityka).
        resolve: Resolver nazw. Wstrzykiwany jak w każdej innej drodze wychodzącej —
            dzięki temu testy nie odpytują realnego DNS-u, a pin IP daje się sprawdzić
            deterministycznie.
    """

    def __init__(self, config: HusarzConfig, *, resolve: HostResolver | None = None) -> None:
        self._config = config
        self._resolve: HostResolver = resolve if resolve is not None else default_resolve

    def rozmiar(self, nazwa: str) -> tuple[RozmiarModelu | None, str]:
        """Czyta manifest i sumuje rozmiary warstw. Nie pobiera wag."""
        rejestr = self._config.bootstrap.registry
        if not rejestr:
            return None, "brak `bootstrap.registry` w konfiguracji"
        url, powod = self._url_manifestu(rejestr, nazwa)
        if url is None:
            return None, powod
        return self._pobierz_manifest(url, nazwa)

    def _url_manifestu(self, rejestr: str, nazwa: str) -> tuple[str | None, str]:
        """Składa adres manifestu i sprawdza go wobec `bootstrap.sources`."""
        from urllib.parse import urlsplit  # noqa: PLC0415

        host = (urlsplit(rejestr).hostname or "").lower()
        dozwolone = self._config.bootstrap.sources
        # Dopasowanie dokładne albo po domenie nadrzędnej — jak w bramce egress.
        if not any(host == d or host.endswith(f".{d}") for d in dozwolone):
            return None, (
                f"host '{host}' nie jest na liście `bootstrap.sources` — zgoda na odczyt "
                f"rozmiaru z tego rejestru nie została udzielona"
            )
        repo, _, tag = nazwa.partition(":")
        if "/" not in repo:
            # Rejestr Ollamy trzyma modele oficjalne w przestrzeni `library`.
            repo = f"library/{repo}"
        return f"{rejestr.rstrip('/')}/v2/{repo}/manifests/{tag or 'latest'}", ""

    def _pobierz_manifest(self, url: str, nazwa: str) -> tuple[RozmiarModelu | None, str]:
        """Wykonuje zapytanie o manifest z przypięciem IP i limitem rozmiaru."""
        import httpx  # noqa: PLC0415

        from husarz.core.errors import EgressError  # noqa: PLC0415

        try:
            # Pin IP (ADR-0020) obowiązuje tę drogę tak samo jak każdą inną wychodzącą:
            # nazwa rejestru nie może rozwiązać się na adres metadanych chmury ani na
            # zakres wewnętrzny operatora. Rejestr modeli jest w WAN, więc bez wyjątków.
            cel = build_pinned_target(
                url, allow_loopback=False, allow_lan=False, resolve=self._resolve
            )
        except EgressError as exc:
            return None, f"adres rejestru odrzucony przez kontrolę anty-SSRF: {exc}"

        naglowki = {"Accept": "application/vnd.docker.distribution.manifest.v2+json"}
        if cel.host_header is not None:
            naglowki["Host"] = cel.host_header
        rozszerzenia = {}
        if cel.sni_hostname is not None:
            rozszerzenia["sni_hostname"] = cel.sni_hostname
        try:
            with httpx.Client(
                timeout=_TIMEOUT_MANIFESTU,
                follow_redirects=False,
                verify=True,
                trust_env=False,
            ) as klient:
                odp = klient.get(cel.connect_url, headers=naglowki, extensions=rozszerzenia)
            if odp.status_code == 404:
                return None, (
                    f"rejestr nie zna modelu '{nazwa}'. Jeśli powstaje on lokalnie "
                    f"z Modelfile (jak `husarz`), użyj `ollama create` — patrz ollama/README.md"
                )
            if odp.status_code != 200:
                return None, f"rejestr odpowiedział kodem {odp.status_code}"
            if len(odp.content) > _LIMIT_MANIFESTU:
                return None, "odpowiedź rejestru jest podejrzanie duża jak na manifest"
            dane = json.loads(odp.content)
        except Exception:  # noqa: BLE001 - treści nie logujemy: URL bywa w komunikacie
            return None, "nie udało się odczytać manifestu (sieć albo format odpowiedzi)"

        warstwy = dane.get("layers") if isinstance(dane, dict) else None
        if not isinstance(warstwy, list) or not warstwy:
            return None, "manifest nie zawiera listy warstw"
        bajty = sum(int(w.get("size", 0)) for w in warstwy if isinstance(w, dict))
        if bajty <= 0:
            return None, "manifest nie podaje rozmiaru warstw"
        return RozmiarModelu(bajty=bajty, warstw=len(warstwy)), ""

    def pobierz(self, endpoint: str, nazwa: str, postep: Callable[[str], None]) -> str:
        """Prosi silnik o pobranie wag (``POST /api/pull``) i raportuje postęp."""
        import httpx  # noqa: PLC0415

        from husarz.router.egress import EgressError, check_endpoint_allowed  # noqa: PLC0415

        try:
            # Adres silnika to infrastruktura operatora — ta sama bramka, co ruch routera.
            check_endpoint_allowed(endpoint, self._config.security.egress)
        except EgressError as exc:
            return f"polityka egress nie pozwala odezwać się do silnika: {exc}"

        baza = endpoint.rstrip("/").removesuffix("/v1")
        ostatni_procent = -1
        # Bez limitu CAŁKOWITEGO — pobranie wag legalnie trwa kwadranse i twardy próg
        # przerywałby poprawne operacje. Limit ODCZYTU zostaje: strumień bez ani jednego
        # bajtu przez pięć minut to zawieszone połączenie, a nie wolne łącze.
        limity = httpx.Timeout(None, connect=15.0, read=300.0)
        try:
            with (
                httpx.Client(timeout=limity, trust_env=False) as klient,
                klient.stream(
                    "POST", f"{baza}/api/pull", json={"model": nazwa, "stream": True}
                ) as odp,
            ):
                if odp.status_code != 200:
                    return f"silnik odpowiedział kodem {odp.status_code}"
                for linia in odp.iter_lines():
                    if not linia:
                        continue
                    try:
                        zdarzenie = json.loads(linia)
                    except ValueError:
                        continue
                    if blad := zdarzenie.get("error"):
                        return str(blad)[:200]
                    calosc = zdarzenie.get("total")
                    zrobione = zdarzenie.get("completed")
                    if isinstance(calosc, int) and isinstance(zrobione, int) and calosc:
                        procent = zrobione * 100 // calosc
                        # Raportujemy co 10%, nie co pakiet: strumień Ollamy potrafi
                        # dać tysiące zdarzeń, a terminal ma pozostać czytelny.
                        if procent >= ostatni_procent + 10:
                            ostatni_procent = procent - procent % 10
                            postep(f"     {procent:3d}% ({zrobione / 1e9:.2f} GB)")
        except Exception:  # noqa: BLE001 - treści nie logujemy (URL w komunikacie transportu)
            return "połączenie z silnikiem zawiodło w trakcie pobierania"
        return ""
