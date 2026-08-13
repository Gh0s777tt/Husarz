# web/ — interfejs WWW (Etap 5)

Własne UI Husarza: **czat + panel konfiguracji + podgląd audytu + monitor tokenów**.
Planowany stos: Next.js/React. Implementacja w **Etapie 5**.

UI komunikuje się z rdzeniem przez REST/WS API (`husarz.api`). Panel konfiguracji
zapisuje nadpisania runtime (najwyższy priorytet w hierarchii konfiguracji), z
walidacją tym samym schematem co pliki YAML.
