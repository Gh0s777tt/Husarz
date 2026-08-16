#!/usr/bin/env python3
"""Odświeżanie zrzutów ekranu konsoli WWW do dokumentacji (`docs/assets/screenshots/`).

CLAUDE.md wymaga, by wiki i PDF nie były samym tekstem — zrzuty muszą pochodzić z REALNIE
uruchomionej aplikacji i być aktualne. Ręczne robienie ich przy każdej zmianie UI jest
zawodne (łatwo przeoczyć rozjazd zrzut↔kod), więc proces jest skryptowany i powtarzalny.

Skrypt NIE jest zależnością projektu ani nie działa w CI — to narzędzie operatora.
Wymaga `playwright` (`pip install playwright`) oraz zainstalowanego Google Chrome; używamy
kanału `chrome`, żeby nie ściągać osobnego Chromium.

Użycie::

    # 1. uruchom Husarza (osobny terminal)
    python -m husarz.launcher.cli up --host 127.0.0.1 --port 8000

    # 2. odśwież zrzuty
    python scripts/screenshots.py --base-url http://127.0.0.1:8000

UWAGA BEZPIECZEŃSTWA: zrzuty trafiają do PUBLICZNEGO repozytorium. Rób je wyłącznie na
instancji z danymi demonstracyjnymi. Przed commitem obejrzyj każdy plik — UI potrafi
pokazać ścieżki, adresy, nazwy kont czy fragmenty tokenów. Skrypt sam tego nie oceni.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from playwright.sync_api import Page, sync_playwright
except ImportError:  # pragma: no cover - narzędzie operatora, nie kod produkcyjny
    print(
        "Brak playwrighta. Zainstaluj narzędzie operatora: pip install playwright",
        file=sys.stderr,
    )
    raise SystemExit(2) from None

# Rozmiar okna dobrany tak, by zrzut był czytelny w dokumentacji i w PDF (bez zbędnego
# marginesu). Zmiana wymaga odświeżenia WSZYSTKICH zrzutów naraz — inaczej strona
# dokumentacji miesza dwa rozmiary.
VIEWPORT = {"width": 1280, "height": 900}

# Pytanie zadawane modelowi na zrzucie czatu. Świadomie krótkie i deterministyczne
# w treści (kod w Pythonie), żeby zrzut pokazywał podświetlanie składni i przycisk „kopiuj".
CHAT_PROMPT = "Napisz funkcję w Pythonie, która liczy sumę liczb parzystych z listy."

# Ile czekamy na odpowiedź lokalnego modelu. 7B na CPU potrafi liczyć kilkadziesiąt sekund.
CHAT_TIMEOUT_MS = 300_000


# Panele zakładek są w HTML wypełnione literałem „…" i podmieniane dopiero po odpowiedzi
# z API. Czekanie na `networkidle` NIE wystarcza (fetch startuje po kliknięciu, a sieć bywa
# już bezczynna) — dlatego czekamy, aż znacznik panelu przestanie być placeholderem.
PLACEHOLDER = "…"


@dataclass(frozen=True, slots=True)
class Shot:
    """Pojedynczy zrzut: zakładka konsoli i plik docelowy.

    Attributes:
        tab: wartość atrybutu ``data-tab`` przycisku zakładki w konsoli.
        filename: nazwa pliku w ``docs/assets/screenshots/``.
        description: opis do komunikatu na stdout (po co ten zrzut istnieje).
        ready_id: ``id`` elementu, który po dociągnięciu danych przestaje być
            placeholderem; ``None`` dla zakładek bez ładowania z API (czat).
    """

    tab: str
    filename: str
    description: str
    ready_id: str | None = None


SHOTS: tuple[Shot, ...] = (
    Shot("chat", "console.png", "czat z lokalnym modelem (ekran główny)"),
    Shot("agents", "console-agenci.png", "lista agentów Chorągwi", ready_id="agents-out"),
    Shot("audit", "console-audyt.png", "audyt z weryfikacją łańcucha", ready_id="audit-out"),
    Shot("usage", "console-monitor.png", "monitor zużycia tokenów", ready_id="usage-out"),
)


def _open_tab(page: Page, shot: Shot) -> None:
    """Przełącza konsolę na zakładkę zrzutu i czeka na dociągnięcie danych.

    Args:
        page: strona konsoli.
        shot: opis zrzutu (zakładka + znacznik gotowości).

    Raises:
        TimeoutError: gdy panel nie wypełni się danymi w 30 s.
    """
    page.click(f'nav button[data-tab="{shot.tab}"]')
    if shot.ready_id is None:
        return
    page.wait_for_function(
        "([id, ph]) => { const el = document.getElementById(id);"
        " return el && el.innerText.trim() !== ph && el.innerText.trim() !== ''; }",
        arg=[shot.ready_id, PLACEHOLDER],
        timeout=30_000,
    )


def _run_chat(page: Page) -> None:
    """Zadaje modelowi pytanie i czeka na pełną odpowiedź.

    Zrzut czatu ma pokazywać realną odpowiedź modelu — ekran powitalny nie dokumentuje
    niczego. Na czas oczekiwania konsola wstawia ``<span class="typing">Husarz pisze…</span>``
    i podmienia go na treść po odpowiedzi; znikniecie tego znacznika jest DOKŁADNYM sygnałem
    końca. (Wcześniejsza wersja czekała, aż długość tekstu przestanie rosnąć — heurystyka
    stabilizowała się na samym placeholderze i zrzut łapał „Husarz pisze…".)

    Raises:
        TimeoutError: gdy model nie odpowie w ``CHAT_TIMEOUT_MS``.
    """
    page.fill("#msg", CHAT_PROMPT)
    page.click("#send")
    page.wait_for_selector("#chat-log .typing", timeout=30_000)
    page.wait_for_selector("#chat-log .typing", state="detached", timeout=CHAT_TIMEOUT_MS)
    # Przewijamy tak, by w kadrze był blok kodu, a nie sam koniec wywodu.
    page.evaluate("""() => {
            const log = document.getElementById('chat-log');
            const answer = log.querySelectorAll('.msg.assistant')[1];
            log.scrollTop = answer.offsetTop - 90;
        }""")


def capture(base_url: str, out_dir: Path) -> int:
    """Robi komplet zrzutów konsoli.

    Args:
        base_url: adres uruchomionej konsoli (np. ``http://127.0.0.1:8000``).
        out_dir: katalog docelowy na pliki PNG.

    Returns:
        Liczba zapisanych zrzutów.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome")
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(base_url, wait_until="networkidle")
        _run_chat(page)
        for shot in SHOTS:
            _open_tab(page, shot)
            target = out_dir / shot.filename
            page.screenshot(path=str(target))
            print(f"  {shot.filename:<24} — {shot.description}")
            saved += 1
        browser.close()
    return saved


def main(argv: list[str] | None = None) -> int:
    """Punkt wejścia CLI."""
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="adres uruchomionej konsoli Husarza (domyślnie: %(default)s)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=repo_root / "docs" / "assets" / "screenshots",
        help="katalog docelowy (domyślnie: docs/assets/screenshots)",
    )
    args = parser.parse_args(argv)

    print(f"Zrzuty z {args.base_url} → {args.out}")
    saved = capture(args.base_url, args.out)
    print(
        f"Zapisano {saved} zrzut(y/ów). PRZEJRZYJ je przed commitem — repozytorium jest publiczne."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
