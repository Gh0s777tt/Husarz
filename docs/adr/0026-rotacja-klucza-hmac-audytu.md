# ADR-0026 — Rotacja klucza HMAC dziennika audytu

- **Status**: przyjęty
- **Data**: 2026-08-28
- **Kontekst**: Etap 18
- **Powiązane**: [ADR-0023](0023-zapisywalny-magazyn-sekretow.md),
  [BEZPIECZENSTWO — Etap 17n/17o](../BEZPIECZENSTWO.md), [Etap 18](../BEZPIECZENSTWO.md)

## Problem

Dziennik audytu można było chronić kluczem HMAC (`security.audit.hmac_key_ref`), ale nie
można było tego klucza **wymienić**. Zmiana referencji zachowywała się jak włączenie HMAC po
raz pierwszy: istniejące wpisy nie weryfikowały się nowym kluczem, więc Husarz odmawiał
startu, a jedyną drogą naprzód było zarchiwizowanie dziennika i założenie pustego.

Skutek był przewrotny. Rotacja klucza to zalecana praktyka higieny, zwłaszcza po podejrzeniu
wycieku — a w Husarzu jej ceną była **utrata całej historii audytu dokładnie wtedy, gdy jest
najbardziej potrzebna**. Racjonalny operator nie rotuje klucza, czyli zostaje przy kluczu,
któremu przestał ufać.

## Rozważane rozwiązania

### A. Ponowne podpisanie historii nowym kluczem

Odrzucone, i to nie z powodu kosztu. Przeliczenie skrótów wszystkich wpisów nowym kluczem
jest operacją **nieodróżnialną od ataku**: gdyby Husarz potrafił to zrobić, potrafiłby też
przekuć dowolną historię pod dyktando kogoś, kto zdobył klucz. Mechanizm naprawczy nie może
być zarazem gotowym narzędziem fałszerstwa.

### B. Odcisk klucza we wpisie (`sha256(klucz)` skrócony)

Odrzucone. Dowolna funkcja materiału klucza zapisana w dzienniku — a dziennik bywa czytelny
szerzej niż klucz — pozwala **offline potwierdzać zgadywane klucze**. Dla klucza o wysokiej
entropii to problem teoretyczny, ale nic nie wymusza wysokiej entropii, a projekt nie ma
prawa zakładać, że operator nie użyje hasła.

### C. Etykieta pokolenia nadana przez operatora — **wybrane**

Wpis niesie `key_id`: nazwę nadaną przez człowieka (`"2026-08"`), która o samym kluczu nie
mówi nic. Konfiguracja wskazuje klucz bieżący (podpisuje nowe wpisy) i listę kluczy
wcześniejszych pokoleń (wyłącznie do weryfikacji).

## Decyzja

```yaml
audit:
  hmac_key_ref: "env:HUSARZ_AUDIT_HMAC_2026_08"   # podpisuje NOWE wpisy
  hmac_key_id: "2026-08"                           # etykieta zapisywana we wpisach
  hmac_verify_keys:                                # WYŁĄCZNIE do weryfikacji historii
    - id: ""                                       # wpisy sprzed pierwszej rotacji
      ref: "env:HUSARZ_AUDIT_HMAC_2026_02"
```

Etykieta pusta (`id: ""`) oznacza wpisy powstałe, **zanim pole `key_id` w ogóle istniało**.
Nie da się im nadać nazwy wstecz, bo zmieniłaby ich skróty — a to jest właśnie ta operacja,
której odmawiamy w wariancie A.

### Reguła niemalejącego pokolenia — sedno, a nie szczegół

Samo dobieranie klucza po etykiecie byłoby **księgowością, nie zabezpieczeniem**. Gdyby na
tym poprzestać, posiadacz klucza WYCOFANEGO — czyli dokładnie ten, przed kim rotacja ma
chronić — mógłby dopisać albo przepisać końcówkę dziennika, oznaczając własne wpisy starym
pokoleniem. Etykieta jest przecież polem wpisu, a stary klucz nadal jest akceptowany, bo
służy do czytania historii.

Dlatego pokolenia są **uporządkowane** (kolejność listy: od najstarszego), a `verify()`
wymaga, by idąc po łańcuchu indeks pokolenia **nie malał**. Wpis oznaczony starszym
pokoleniem po wpisie nowszego jest odrzucany.

Sprawdzone uruchomieniem, nie rozumowaniem: napastnik z wycofanym kluczem konstruuje wpis
poprawny kryptograficznie (jego skrót zgadza się pod starym kluczem) i aktualizuje kotwicę,
by nie zdradziła dopisku — `verify()` i tak zwraca `False`. Test:
`tests/security/test_audyt_rotacja.py::test_wycofany_klucz_NIE_dopisze_sie_do_koncowki`.

### Znacznik rotacji zamyka okno

Reguła potrzebuje czegoś, o co się oprze: **dopóki cały dziennik należy do jednego
pokolenia, chroni go wyłącznie klucz tego pokolenia**. Zaraz po rotacji nie ma jeszcze
żadnego wpisu nowego pokolenia, więc okno stoi otworem.

Dlatego `build_audit_log` przy pierwszym starcie po rotacji dopisuje wpis
`audit.key_rotated` (tylko etykiety pokoleń — nigdy materiał). Powstaje raz; kolejne starty
go nie powielają.

### Dwa dodatkowe domknięcia

1. **`key_id` jest częścią payloadu** (gdy niepusty), więc wpisu nie da się „awansować" do
   nowszego pokolenia bez unieważnienia skrótu. Ma to znaczenie tylko wtedy, gdy dwa
   pokolenia dzielą materiał klucza — i właśnie dlatego:
2. **`build_audit_log` odrzuca dwa pokolenia o tym samym materiale.** Schemat by tego nie
   wychwycił: widzi wyłącznie referencje, a dwie różne referencje mogą wskazywać ten sam
   sekret. Kontrola musi więc nastąpić po ich rozwiązaniu.

## Czego to NIE daje — wprost

Pierwsza wersja tego dokumentu mówiła, że reguła „nie pozwala posiadaczowi wycofanego klucza
dopisać ani **przepisać** końcówki". Przegląd adwersaryjny wykazał, że drugie słowo było
za mocne, i sekcja została poprawiona.

- **Reguła broni przed DOPISANIEM, nie przed USUNIĘCIEM.** Odrzuca wpis starszego pokolenia
  postawiony za wpisem nowszego — ale te nowsze wpisy leżą w pliku, do którego napastnik
  musi mieć prawo zapisu, żeby w ogóle cokolwiek dopisać. Może je więc usunąć i cofnąć
  dziennik do własnej ery. Przy DZIAŁAJĄCYM procesie skurczenie pliku jest wykrywane
  i odrzucane, a kotwica ma zapadkę (Etap 18c); przed zimnym startem na już spreparowanym
  pliku chroni dopiero nadzór ZEWNĘTRZNY — kopia dziennika poza maszyną albo wysyłka do
  systemu zbierającego. Pozycja jest w ROADMAP.
- **Kotwica nie jest uwierzytelniona, a jej BRAK liczy się jak zgodność.** Faktyczna
  poprzeczka dla napastnika to nie „usuń linie i podrób kotwicę", lecz „usuń linie i usuń
  kotwicę". Utrata tej kontroli jest od Etapu 18 WIDOCZNA (`stan_kotwicy`, pole `kotwica`
  w `GET /api/audit`), ale widoczność jest sygnałem dla operatora, nie przeszkodą.
- **Rotacja zabezpiecza to, co po niej, a nie to, co przed nią.** Wpisy pokolenia N chroni
  klucz pokolenia N. Jeśli wyciekł, historia sprzed rotacji pozostaje podatna na przekucie —
  reguła pokoleń nie działa wstecz i nie może.
- **Etykieta jest jawna.** Nazwa nadana przez operatora trafia do dziennika, więc nie należy
  w niej umieszczać niczego wrażliwego.
- **Kto ma klucz BIEŻĄCY, może wszystko.** Żaden mechanizm w tym pliku tego nie zmienia.

## Warunek operacyjny: rotacja przy ZATRZYMANYCH instancjach

Reguła niemalejącego pokolenia jest nieodwracalna, a to ma konsekwencję, której kod nie
wykryje. Jeśli po podmianie konfiguracji zadziała jeszcze stary proces — serwer albo
polecenie CLI, bo obie drogi piszą do audytu — dopisze wpis pokolenia STARSZEGO za
znacznikiem rotacji. Dziennik przestanie się wtedy weryfikować **na stałe**, a komunikat
powie, że ktoś przepisał historię, choć nikt niczego nie fałszował.

Dla weryfikatora oba przypadki wyglądają identycznie i nie da się ich rozróżnić — to ta sama
nieusuwalna dwuznaczność, co przy włączaniu HMAC po raz pierwszy. Dlatego jest to wymóg
procedury, a nie kontrola w kodzie: **zatrzymaj wszystkie instancje, podmień konfigurację,
uruchom ponownie.**

## Konsekwencje

- Rotacja przestała kosztować historię audytu.
- Przybyły trzy pola konfiguracji (`hmac_key_id`, `hmac_verify_keys`, `integrity`) — każde
  z czytelnikiem w kodzie i testem, zgodnie z lekcją Etapu 17m o polach, które kłamią.
- Wpisy audytu zyskały pole `key_id`. Dzienniki sprzed tej zmiany weryfikują się bez zmian:
  pole trafia do payloadu wyłącznie, gdy jest niepuste — ta sama sztuczka, co przy
  `principal` (Etap 13b).
