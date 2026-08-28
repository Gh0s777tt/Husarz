"""Niemodyfikowalny dziennik audytu z łańcuchem skrótów (tamper-evidence).

Każdy wpis zawiera skrót ``sha256(prev_hash + kanoniczny_payload)``. Zmiana
dowolnego wcześniejszego wpisu unieważnia wszystkie kolejne skróty, więc
``verify`` wykrywa manipulację. Zapis jest tylko dopisujący (append-only).
Zegar jest wstrzykiwalny — testy są deterministyczne.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from husarz.config.schema import SecurityConfig
from husarz.security.errors import AuditError

GENESIS_HASH = "0" * 64


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True, frozen=True)
class AuditEntry:
    """Pojedynczy, niezmienny wpis audytu."""

    timestamp: str
    actor: str
    action: str
    detail: dict[str, Any]
    roe_ref: str | None
    prev_hash: str
    entry_hash: str
    # KTO zlecił akcję (referencja konta/tokenu, nie nazwa użytkownika — bez PII w logu,
    # który jest niemodyfikowalny). Puste = brak uwierzytelnienia albo wpis sprzed Etapu 13b.
    principal: str = ""


def _payload(
    timestamp: str,
    actor: str,
    action: str,
    detail: dict[str, Any],
    roe_ref: str | None,
    prev_hash: str,
    principal: str = "",
) -> str:
    """Buduje kanoniczny payload wpisu do policzenia skrótu łańcucha.

    ``principal`` trafia do payloadu WYŁĄCZNIE, gdy jest niepusty. To celowe i ma dwie
    konsekwencje, obie pożądane:

    1. **Zgodność wstecz** — dzienniki sprzed dodania tego pola hashują się dokładnie tak
       jak wcześniej, więc ``verify`` na starym pliku nadal przechodzi (inaczej dodanie
       kolumny wyglądałoby jak manipulacja całą historią).
    2. **Tamper-evidence bez luki** — dopisanie albo usunięcie ``principal`` w istniejącym
       wpisie zmienia payload, więc skrót przestaje pasować. Nie da się „odpiąć" wywołania
       od użytkownika ani podpiąć go pod kogo innego bez wykrycia.
    """
    data: dict[str, Any] = {
        "timestamp": timestamp,
        "actor": actor,
        "action": action,
        "detail": detail,
        "roe_ref": roe_ref,
        "prev_hash": prev_hash,
    }
    if principal:
        data["principal"] = principal
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass(slots=True)
class AuditLog:
    """Dopisujący dziennik audytu z weryfikowalnym łańcuchem skrótów.

    Bez ``hmac_key`` łańcuch (goły SHA-256) jest tamper-evident wobec przypadkowej
    korekty. Z ``hmac_key`` (klucz spoza logu) staje się odporny także na
    zmotywowanego edytora bez klucza — zalecane w produkcji.

    **Łańcuch NIE wykrywa odcięcia OGONA.** Usunięcie ostatnich linii pliku zostawia
    prefiks, który jest wewnętrznie spójny — ``verify()`` zwracało ``True``, choć wpisy
    zniknęły. Odtworzone pomiarem: dziennik z pięcioma wpisami po usunięciu dwóch ostatnich
    nadal przechodził weryfikację. Dla dziennika opisywanego jako „niemodyfikowalny" to
    poważna luka: najłatwiejszym sposobem zatarcia śladu jest właśnie usunięcie końcówki,
    a nie edycja w środku.

    Stąd **kotwica** (``anchor_path``): plik obok dziennika, w którym trzymamy liczbę wpisów
    i skrót ostatniego z nich. Przy wczytaniu porównujemy — brakujące wpisy albo przepisana
    historia stają się widoczne.

    Czego kotwica NIE daje (mówimy to wprost): kto ma prawo zapisu do katalogu dziennika,
    ten ma je też do kotwicy. Podnosi to poprzeczkę z „usuń linie" do „usuń linie i zaktualizuj
    kotwicę", i wykrywa awarie przypadkowe (urwany zapis, nieudana rotacja), ale nie zastępuje
    ``hmac_key`` trzymanego POZA systemem plików. Te dwa mechanizmy są komplementarne.
    """

    path: Path | None = None
    clock: Callable[[], datetime] = _default_clock
    hmac_key: bytes | None = None
    # Ścieżka kotwicy. ``None`` = bez kotwicy (zachowanie sprzed jej wprowadzenia).
    anchor_path: Path | None = None
    _entries: list[AuditEntry] = field(default_factory=list)
    _last_hash: str = GENESIS_HASH
    # Serializuje dopisywanie: endpointy FastAPI (zwykłe ``def``) biegną w puli
    # wątków, więc read-modify-write łańcucha skrótów musi być atomowe — inaczej
    # dwa równoległe wpisy dostają ten sam ``prev_hash`` i ``verify`` daje fałszywy
    # alarm manipulacji. Wykluczony z porównań/reprezentacji dataclass.
    _lock: threading.Lock = field(default_factory=threading.Lock, compare=False, repr=False)

    def _compute_hash(self, payload: str) -> str:
        data = payload.encode("utf-8")
        if self.hmac_key is not None:
            return hmac.new(self.hmac_key, data, hashlib.sha256).hexdigest()
        return hashlib.sha256(data).hexdigest()

    @property
    def entries(self) -> list[AuditEntry]:
        """Kopia listy wpisów (tylko do odczytu z zewnątrz)."""
        return list(self._entries)

    @property
    def head_hash(self) -> str:
        """Skrót ostatniego wpisu (lub genesis, gdy pusty)."""
        return self._last_hash

    def record(
        self,
        actor: str,
        action: str,
        detail: dict[str, Any] | None = None,
        *,
        roe_ref: str | None = None,
        principal: str = "",
    ) -> AuditEntry:
        """Dopisuje wpis i zwraca go. Zapis do pliku PRZED mutacją pamięci.

        Args:
            actor: kto WYKONAŁ (agent albo ``api``).
            action: nazwa zdarzenia.
            detail: szczegóły (bez surowej treści/sekretów — skróty i rozmiary).
            roe_ref: identyfikator zlecenia ROE, jeśli dotyczy.
            principal: kto ZLECIŁ (referencja konta/tokenu). Rozdzielenie „kto wykonał"
                od „kto zlecił" jest istotą rozliczalności przy wielu użytkownikach —
                sam ``actor='kopijnik'`` nie odpowiada na pytanie, czyje to było żądanie.
        """
        # Głęboka kopia — treść nie może zmienić się po zahashowaniu (niezmienność).
        safe_detail = copy.deepcopy(dict(detail or {}))
        # Cała sekwencja (odczyt prev_hash → hash → zapis pliku → mutacja pamięci)
        # jest krytyczna sekcją: gwarantuje ciągłość łańcucha pod współbieżnością.
        with self._lock:
            timestamp = self.clock().isoformat()
            try:
                payload = _payload(
                    timestamp, actor, action, safe_detail, roe_ref, self._last_hash, principal
                )
            except (TypeError, ValueError) as exc:
                raise AuditError(f"Szczegóły audytu nie są serializowalne do JSON: {exc}") from exc
            entry = AuditEntry(
                timestamp=timestamp,
                actor=actor,
                action=action,
                detail=safe_detail,
                roe_ref=roe_ref,
                prev_hash=self._last_hash,
                entry_hash=self._compute_hash(payload),
                principal=principal,
            )
            # Persist-first: jeśli zapis pliku zawiedzie, stan w pamięci NIE rozjeżdża się.
            self._append_to_file(entry)
            self._entries.append(entry)
            self._last_hash = entry.entry_hash
            # Kotwica PO wpisie, nie przed. Kolejność jest istotna dla awarii: przerwanie
            # między jednym a drugim zostawia dziennik O KROK DO PRZODU względem kotwicy,
            # co jest stanem bezpiecznym (nic nie zginęło). Odwrotna kolejność zostawiałaby
            # kotwicę wskazującą na wpis, którego nie ma — czyli fałszywy alarm manipulacji
            # po zwykłym zaniku zasilania.
            self._zapisz_kotwice()
        return entry

    def _zapisz_kotwice(self) -> None:
        """Utrwala liczbę wpisów i skrót ostatniego — poza plikiem dziennika.

        Błąd zapisu kotwicy NIE przerywa audytu: wpis jest już bezpiecznie na dysku,
        a kotwica to warstwa dodatkowa. Przerwanie działania w tym miejscu zamieniłoby
        ulepszenie wykrywalności w nową awarię ścieżki krytycznej.
        """
        if self.anchor_path is None:
            return
        try:
            self.anchor_path.parent.mkdir(parents=True, exist_ok=True)
            tymczasowy = self.anchor_path.with_suffix(self.anchor_path.suffix + ".tmp")
            tymczasowy.write_text(
                json.dumps({"wpisow": len(self._entries), "skrot": self._last_hash}),
                encoding="utf-8",
            )
            # Podmiana atomowa: kotwica nigdy nie jest widziana w stanie połowicznym.
            tymczasowy.replace(self.anchor_path)
        except OSError:  # pragma: no cover - rzadka ścieżka I/O
            return

    def verify(self) -> bool:
        """Sprawdza integralność dziennika: łańcuch skrótów ORAZ kompletność wobec kotwicy.

        Sam łańcuch nie wystarcza — patrz docstring klasy: odcięcie ogona zostawia prefiks
        wewnętrznie spójny, więc do wprowadzenia kotwicy ta metoda meldowała „brak
        manipulacji" na dzienniku, z którego usunięto wpisy.

        Returns:
            ``True``, gdy łańcuch jest spójny i nic nie zniknęło.
        """
        if not self._kompletny_wobec_kotwicy():
            return False
        prev = GENESIS_HASH
        for entry in self._entries:
            if entry.prev_hash != prev:
                return False
            payload = _payload(
                entry.timestamp,
                entry.actor,
                entry.action,
                entry.detail,
                entry.roe_ref,
                prev,
                entry.principal,
            )
            if self._compute_hash(payload) != entry.entry_hash:
                return False
            prev = entry.entry_hash
        return True

    def _kompletny_wobec_kotwicy(self) -> bool:
        """Czy dziennik zawiera wszystko, co kotwica widziała ostatnio.

        Trzy rozstrzygnięcia, każde z uzasadnieniem:

        * **mniej wpisów niż w kotwicy** → ODCIĘCIE. Wpisy zniknęły.
        * **tyle samo lub więcej, ale skrót na pozycji kotwicy się nie zgadza** → historia
          została PRZEPISANA. Sama liczba wpisów by tego nie złapała: napastnik mógłby
          usunąć końcówkę i dopisać własną o tej samej długości.
        * **więcej wpisów, skrót się zgadza** → w porządku. Dziennik wyprzedza kotwicę,
          co zdarza się po przerwaniu między zapisem wpisu a zapisem kotwicy — nic nie zginęło.

        Returns:
            ``True``, gdy brak kotwicy albo gdy dziennik jest wobec niej kompletny.
        """
        if self.anchor_path is None or not self.anchor_path.is_file():
            return True
        try:
            dane = json.loads(self.anchor_path.read_text(encoding="utf-8"))
            oczekiwane = int(dane["wpisow"])
            skrot = str(dane["skrot"])
        except (OSError, ValueError, KeyError, TypeError):
            # Nieczytelna kotwica NIE może unieważniać dziennika — to byłby fałszywy alarm
            # manipulacji po zwykłym uszkodzeniu pliku pomocniczego.
            return True
        if len(self._entries) < oczekiwane:
            return False
        if oczekiwane == 0:
            return True
        return self._entries[oczekiwane - 1].entry_hash == skrot

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        hmac_key: bytes | None = None,
        anchor_path: str | Path | None = None,
    ) -> AuditLog:
        """Wczytuje dziennik z pliku JSONL i odtwarza łańcuch (do ``verify``)."""
        source = Path(path)
        log = cls(
            path=None,
            hmac_key=hmac_key,
            anchor_path=Path(anchor_path) if anchor_path is not None else None,
        )
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            entry = AuditEntry(**json.loads(line))
            log._entries.append(entry)
            log._last_hash = entry.entry_hash
        return log

    def _append_to_file(self, entry: AuditEntry) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        except OSError as exc:  # pragma: no cover - rzadka ścieżka I/O
            raise AuditError(f"Nie można dopisać do audytu: {exc}") from exc


def build_audit_log(security: SecurityConfig) -> AuditLog:
    """Buduje dziennik audytu z konfiguracji (ścieżka z ``security.audit``).

    Gdy plik już istnieje, odtwarza z niego łańcuch — dopiski po restarcie procesu
    pozostają ciągłe (bez fałszywego genesis w środku pliku).
    """
    audit = security.audit
    if not audit.enabled:
        return AuditLog(path=None)
    path = Path(audit.path)
    # Kotwica leży obok dziennika i ma tę samą nazwę z przyrostkiem. Osobny plik, bo musi
    # przetrwać odcięcie ogona dziennika — trzymanie jej W dzienniku niczego by nie dało.
    log = AuditLog(path=path, anchor_path=path.with_name(path.name + ".kotwica"))
    if path.exists():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = AuditEntry(**json.loads(line))
                    log._entries.append(entry)
                    log._last_hash = entry.entry_hash
        except (OSError, ValueError):  # pragma: no cover - uszkodzony plik audytu
            pass
    return log
