# web/ — interfejs WWW

## Stan obecny (Etap 5): konsola MVP serwowana przez API

Działająca konsola to **jednoplikowa aplikacja** `src/husarz/api/static/console.html`
(vanilla JS, theme-aware), serwowana przez API pod `/` (`husarz up`). Zapewnia:
**czat** (orkiestracja), **panel konfiguracji** (podgląd + walidacja nadpisań),
**agenci**, **audyt** (status łańcucha skrótów) i **monitor** kosztów/tokenów.
Bez kroku budowania — w pełni testowalna (API zwraca HTML). Szczegóły: [../docs/API.md](../docs/API.md).

## Ścieżka produkcyjna (przyszłość): Next.js/React

Bogatszy frontend (Next.js/React) komunikujący się z REST/WS API rdzenia
(`husarz.api`). Panel konfiguracji zapisuje nadpisania runtime (najwyższy priorytet
w hierarchii konfiguracji), z walidacją tym samym schematem co pliki YAML.
